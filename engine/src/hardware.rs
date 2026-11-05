//! Official Buttplug hardware traits backed by Home Assistant GATT RPC.
use crate::bridge::{Advertisement, Bridge, DeviceResult, Session, StopWrite, device_error};
use async_trait::async_trait;
use buttplug_core::ButtplugResultFuture;
use buttplug_server::device::hardware::{
    Hardware, HardwareConnector, HardwareEvent, HardwareInternal, HardwareReadCmd, HardwareReading,
    HardwareSpecializer, HardwareSubscribeCmd, HardwareUnsubscribeCmd, HardwareWriteCmd,
    communication::{
        HardwareCommunicationManager, HardwareCommunicationManagerBuilder,
        HardwareCommunicationManagerEvent,
    },
};
use buttplug_server_device_config::{Endpoint, ProtocolCommunicationSpecifier};
use futures::{FutureExt, future::BoxFuture};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    fmt,
    sync::{Arc, atomic::Ordering},
    time::Duration,
};
use tokio::sync::{broadcast, mpsc};
use uuid::Uuid;

pub struct HaManagerBuilder(pub Arc<Bridge>);
impl HardwareCommunicationManagerBuilder for HaManagerBuilder {
    fn finish(
        &mut self,
        sender: mpsc::Sender<HardwareCommunicationManagerEvent>,
    ) -> Box<dyn HardwareCommunicationManager> {
        self.0.install_manager(sender);
        Box::new(HaManager(self.0.clone()))
    }
}
struct HaManager(Arc<Bridge>);
impl HardwareCommunicationManager for HaManager {
    fn name(&self) -> &'static str {
        "HomeAssistantBluetooth"
    }
    fn can_scan(&self) -> bool {
        self.0.is_connected() && self.0.enabled.load(Ordering::SeqCst)
    }
    fn scanning_status(&self) -> bool {
        self.0.scanning.load(Ordering::SeqCst)
    }
    fn start_scanning(&mut self) -> ButtplugResultFuture {
        let bridge = self.0.clone();
        async move { bridge.scan(true).await.map_err(Into::into) }.boxed()
    }
    fn stop_scanning(&mut self) -> ButtplugResultFuture {
        let bridge = self.0.clone();
        async move { bridge.scan(false).await.map_err(Into::into) }.boxed()
    }
}

pub struct HaConnector {
    bridge: Arc<Bridge>,
    advertisement: Advertisement,
    protocol_hints: Vec<String>,
}
impl HaConnector {
    pub fn new(
        bridge: Arc<Bridge>,
        advertisement: Advertisement,
        protocol_hints: Vec<String>,
    ) -> Self {
        Self {
            bridge,
            advertisement,
            protocol_hints,
        }
    }
}
impl fmt::Debug for HaConnector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HaConnector").finish_non_exhaustive()
    }
}

#[derive(Deserialize)]
struct GattCharacteristic {
    uuid: Uuid,
    #[serde(default)]
    properties: Vec<String>,
}
#[derive(Deserialize)]
struct GattService {
    uuid: Uuid,
    characteristics: Vec<GattCharacteristic>,
}
#[derive(Deserialize)]
struct ConnectResult {
    services: Vec<GattService>,
}

#[async_trait]
impl HardwareConnector for HaConnector {
    fn specifier(&self) -> ProtocolCommunicationSpecifier {
        self.advertisement.specifier()
    }
    async fn connect(&mut self) -> DeviceResult<Box<dyn HardwareSpecializer>> {
        let address = &self.advertisement.address;
        if !self.bridge.enabled.load(Ordering::SeqCst) || !self.bridge.supported(address) {
            return Err(device_error("Device is disabled or unsupported"));
        }
        let _attempt = self
            .bridge
            .begin_connection(address)
            .ok_or_else(|| device_error("A connection attempt is already in progress"))?;
        if self.bridge.session_for(address).is_some() {
            return Err(device_error("Device already has a BLE connection"));
        }
        let id = Uuid::new_v4().to_string();
        // Reaching HardwareConnector::connect means the official Buttplug
        // device manager accepted this advertisement as a viable protocol.
        // Only now expose it to Home Assistant as an Intiface device. This
        // avoids both false positives and IntiHA pre-filtering valid devices.
        let _ = self
            .bridge
            .send(json!({
                "type":"discovered",
                "address":address,
                "name":self.advertisement.name.clone(),
                "protocols":self.protocol_hints.clone(),
                "rssi":self.advertisement.rssi,
                "source":self.advertisement.source.clone()
            }))
            .await;
        self.bridge
            .device_status(address, &id, "connecting", None)
            .await;
        let result = self
            .bridge
            .rpc(
                json!({"op":"connect","address":address,"connection_id":id}),
                Duration::from_secs(50),
            )
            .await;
        let result = match result {
            Ok(value) => value,
            Err(error) => {
                self.bridge.failed_attempt(address);
                let detail = error.to_string();
                self.bridge
                    .device_status(address, &id, "retrying", Some(&detail))
                    .await;
                // A connect may have completed at HA just as the RPC timed out.
                let _ = self
                    .bridge
                    .rpc(
                        json!({"op":"disconnect","address":address,"connection_id":id}),
                        Duration::from_secs(12),
                    )
                    .await;
                return Err(error);
            }
        };
        if !self.bridge.enabled.load(Ordering::SeqCst) || !self.bridge.supported(address) {
            let _ = self
                .bridge
                .rpc(
                    json!({"op":"disconnect","address":address,"connection_id":id}),
                    Duration::from_secs(12),
                )
                .await;
            return Err(device_error("Connection cancelled by configuration change"));
        }
        let result: ConnectResult = match serde_json::from_value(result) {
            Ok(value) => value,
            Err(_) => {
                let _ = self
                    .bridge
                    .rpc(
                        json!({"op":"disconnect","address":address,"connection_id":id}),
                        Duration::from_secs(12),
                    )
                    .await;
                return Err(device_error(
                    "Invalid GATT service response from Home Assistant",
                ));
            }
        };
        let session = Arc::new(Session::new(address, &id, false));
        self.bridge.register(session.clone());
        self.bridge
            .device_status(address, &id, "initialising", None)
            .await;
        Ok(Box::new(HaSpecializer {
            bridge: self.bridge.clone(),
            session,
            name: self.advertisement.name.clone(),
            services: result.services,
        }))
    }
}

struct HaSpecializer {
    bridge: Arc<Bridge>,
    session: Arc<Session>,
    name: String,
    services: Vec<GattService>,
}
#[async_trait]
impl HardwareSpecializer for HaSpecializer {
    async fn specialize(
        &mut self,
        protocols: &[ProtocolCommunicationSpecifier],
    ) -> DeviceResult<Hardware> {
        let Some(ProtocolCommunicationSpecifier::BluetoothLE(specifier)) = protocols
            .iter()
            .find(|p| matches!(p, ProtocolCommunicationSpecifier::BluetoothLE(_)))
        else {
            let _ = self.bridge.disconnect_session(&self.session).await;
            return Err(device_error("No Bluetooth service mapping for this device"));
        };
        let mut endpoints = HashMap::new();
        let mut write_modes = HashMap::new();
        for (service_uuid, characteristic_map) in specifier.services() {
            if let Some(service) = self.services.iter().find(|s| s.uuid == *service_uuid) {
                for (endpoint, uuid) in characteristic_map {
                    if let Some(characteristic) = service
                        .characteristics
                        .iter()
                        .find(|c| c.uuid == *uuid)
                    {
                        endpoints.insert(*endpoint, uuid.to_string());
                        // The real GATT characteristic determines which write mode
                        // can be used for a verified emergency-stop fallback.
                        if characteristic.properties.iter().any(|p| p == "write") {
                            write_modes.insert(uuid.to_string(), true);
                        } else if characteristic
                            .properties
                            .iter()
                            .any(|p| p == "write-without-response")
                        {
                            write_modes.insert(uuid.to_string(), false);
                        }
                    }
                }
            }
        }
        if endpoints.is_empty() {
            let _ = self.bridge.disconnect_session(&self.session).await;
            return Err(device_error(
                "None of the required GATT characteristics was found",
            ));
        }
        let endpoint_list: Vec<_> = endpoints.keys().copied().collect();
        self.session.classic_wevibe.store(
            endpoints
                .values()
                .any(|uuid| uuid.eq_ignore_ascii_case(CLASSIC_WEVIBE_TX)),
            Ordering::SeqCst,
        );
        *self.session.endpoints.write().unwrap() = endpoints;
        *self.session.write_modes.write().unwrap() = write_modes;
        Ok(Hardware::new(
            &self.name,
            &self.session.address,
            &endpoint_list,
            &Some(Duration::from_millis(75)),
            false,
            Box::new(HaHardware {
                bridge: self.bridge.clone(),
                session: self.session.clone(),
            }),
        ))
    }
}

pub struct HaHardware {
    pub bridge: Arc<Bridge>,
    pub session: Arc<Session>,
}
struct OperationGuard(Arc<Session>);
impl Drop for OperationGuard {
    fn drop(&mut self) {
        self.0.inflight.fetch_sub(1, Ordering::SeqCst);
    }
}
impl Drop for HaHardware {
    fn drop(&mut self) {
        if self.session.connected.load(Ordering::SeqCst)
            && let Ok(runtime) = tokio::runtime::Handle::try_current()
        {
            let bridge = self.bridge.clone();
            let session = self.session.clone();
            runtime.spawn(async move {
                let _ = bridge.disconnect_session(&session).await;
            });
        }
    }
}
impl HaHardware {
    fn operation(
        &self,
        endpoint: Endpoint,
        op: &'static str,
        mut args: Value,
    ) -> BoxFuture<'static, DeviceResult<Value>> {
        let bridge = self.bridge.clone();
        let session = self.session.clone();
        let characteristic = session.endpoints.read().unwrap().get(&endpoint).cloned();
        async move {
            let io_guard = session.io_lock.lock().await;
            if !session.connected.load(Ordering::SeqCst) || bridge.session(&session.id).is_none() {
                return Err(device_error(
                    "BLE session has expired; command was not replayed",
                ));
            }
            let characteristic =
                characteristic.ok_or_else(|| device_error("Device endpoint is unavailable"))?;
            args["op"] = op.into();
            args["address"] = session.address.clone().into();
            args["connection_id"] = session.id.clone().into();
            args["characteristic"] = characteristic.into();
            session.inflight.fetch_add(1, Ordering::SeqCst);
            let result = {
                let _guard = OperationGuard(session.clone());
                // HA keeps the actual I/O deadline at 10s. Its failure path
                // may also spend 4s on a cached stop, 5s disconnecting, 10s
                // emitting two lifecycle events and 10s sending the reply.
                // Allow that bounded cleanup/reporting, not just the write.
                // This does NOT extend the physical GATT operation timeout.
                bridge.rpc(args, Duration::from_secs(40)).await
            };
            drop(io_guard);
            if let Err(error) = &result {
                *session.last_io_error.lock().unwrap() = Some(format!("{op}: {error}"));
                // Do not count teardown as an in-flight GATT operation.
                // No motion is replayed after a write with uncertain delivery.
                let _ = bridge.disconnect_session(&session).await;
            }
            result
        }
        .boxed()
    }
}

// The official classic We-Vibe BLE profile has a fixed 8-byte motor-off
// command on this exact characteristic. Do not extrapolate this to other
// devices or other BLE control endpoints.
const CLASSIC_WEVIBE_TX: &str = "f000c000-0451-4000-b000-000000000000";
const CLASSIC_WEVIBE_MOTORS_OFF: [u8; 8] = [0x0f, 0, 0, 0, 0, 0, 0, 0];

/// Return a protocol-verified stop for classic We-Vibe devices only.
/// A connected BLE characteristic and supported write mode are mandatory;
/// there is no generic guessed-byte fallback for unknown protocols.
pub(crate) fn classic_wevibe_stop_write(session: &Session) -> Option<StopWrite> {
    if !session.classic_wevibe.load(Ordering::SeqCst) {
        return None;
    }
    let endpoints = session.endpoints.read().unwrap();
    if !endpoints
        .values()
        .any(|uuid| uuid.eq_ignore_ascii_case(CLASSIC_WEVIBE_TX))
    {
        return None;
    }
    let response = *session.write_modes.read().unwrap().get(CLASSIC_WEVIBE_TX)?;
    Some(StopWrite {
        characteristic: CLASSIC_WEVIBE_TX.into(),
        data: CLASSIC_WEVIBE_MOTORS_OFF.to_vec(),
        response,
    })
}

/// Classic We-Vibe's upstream initialiser briefly requests both motors at 9.
/// Replace only that exact first initialisation write with its documented stop.
pub fn safe_initial_write(
    first: bool,
    classic_wevibe: bool,
    characteristic: &str,
    data: &[u8],
) -> Vec<u8> {
    if first
        && classic_wevibe
        && characteristic.eq_ignore_ascii_case(CLASSIC_WEVIBE_TX)
        && data == [0x0f, 0x03, 0x00, 0x99, 0x00, 0x03, 0x00, 0x00]
    {
        CLASSIC_WEVIBE_MOTORS_OFF.to_vec()
    } else {
        data.to_vec()
    }
}

impl HardwareInternal for HaHardware {
    fn event_stream(&self) -> broadcast::Receiver<HardwareEvent> {
        self.session.events.subscribe()
    }
    fn disconnect(&self) -> BoxFuture<'static, DeviceResult<()>> {
        let bridge = self.bridge.clone();
        let session = self.session.clone();
        async move { bridge.disconnect_session(&session).await }.boxed()
    }
    fn read_value(
        &self,
        message: &HardwareReadCmd,
    ) -> BoxFuture<'static, DeviceResult<HardwareReading>> {
        let endpoint = message.endpoint();
        let result = self.operation(endpoint, "read", json!({}));
        async move {
            let value = result.await?;
            let data: Vec<u8> = serde_json::from_value(value["data"].clone())
                .map_err(|_| device_error("Invalid GATT read data"))?;
            if data.len() > 512 {
                return Err(device_error("GATT read exceeded 512 bytes"));
            }
            Ok(HardwareReading::new(endpoint, &data))
        }
        .boxed()
    }
    fn write_value(&self, message: &HardwareWriteCmd) -> BoxFuture<'static, DeviceResult<()>> {
        let characteristic = self
            .session
            .endpoints
            .read()
            .unwrap()
            .get(&message.endpoint())
            .cloned()
            .unwrap_or_default();
        let data = safe_initial_write(
            self.session.first_write.swap(false, Ordering::SeqCst),
            self.session.classic_wevibe.load(Ordering::SeqCst),
            &characteristic,
            message.data(),
        );
        let write = StopWrite {
            characteristic,
            data: data.clone(),
            response: message.write_with_response(),
        };
        let session = self.session.clone();
        let bridge = self.bridge.clone();
        let result = self.operation(
            message.endpoint(),
            "write",
            json!({"data":data,"response":message.write_with_response()}),
        );
        async move {
            // During stop preparation the official protocol driver compiles
            // its stop into a packet plan; it does NOT send the packet yet.
            // Otherwise a slow GATT write holds up the driver's queue and the
            // stop-plan capture times out before we can even cache a stop.
            // Only unready sessions can enter this mode. Real verification is
            // the separate prepare_stop RPC; this is never a ready signal.
            if !session.connected.load(Ordering::SeqCst)
                || !bridge.enabled.load(Ordering::SeqCst)
                || bridge.session(&session.id).is_none()
            {
                return Err(device_error("Connection expired while preparing a stop"));
            }
            {
                let mut recording = session.stop_recording.lock().unwrap();
                if let Some(writes) = recording.as_mut() {
                    if session.ready.load(Ordering::SeqCst) {
                        return Err(device_error(
                            "Cannot capture stop packets on a ready device",
                        ));
                    }
                    if write.characteristic.is_empty() || writes.len() >= 16 {
                        return Err(device_error("Invalid or oversized generated stop plan"));
                    }
                    writes.push(write);
                    return Ok(());
                }
            }
            result.await?;
            Ok(())
        }
        .boxed()
    }
    fn subscribe(&self, message: &HardwareSubscribeCmd) -> BoxFuture<'static, DeviceResult<()>> {
        let result = self.operation(message.endpoint(), "subscribe", json!({}));
        async move {
            result.await?;
            Ok(())
        }
        .boxed()
    }
    fn unsubscribe(
        &self,
        message: &HardwareUnsubscribeCmd,
    ) -> BoxFuture<'static, DeviceResult<()>> {
        let result = self.operation(message.endpoint(), "unsubscribe", json!({}));
        async move {
            result.await?;
            Ok(())
        }
        .boxed()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stop_fallback_requires_verified_protocol_endpoint_and_write_mode() {
        use buttplug_server_device_config::Endpoint;
        let session = Session::new("AA:BB:CC:DD:EE:01", "test", true);
        assert!(classic_wevibe_stop_write(&session).is_none());
        session
            .endpoints
            .write()
            .unwrap()
            .insert(Endpoint::Tx, CLASSIC_WEVIBE_TX.into());
        assert!(classic_wevibe_stop_write(&session).is_none());
        session
            .write_modes
            .write()
            .unwrap()
            .insert(CLASSIC_WEVIBE_TX.into(), true);
        let stop = classic_wevibe_stop_write(&session).unwrap();
        assert_eq!(stop.data, vec![15, 0, 0, 0, 0, 0, 0, 0]);
        assert_eq!(stop.characteristic, CLASSIC_WEVIBE_TX);
        assert!(stop.response);
        let unrelated = Session::new("AA:BB:CC:DD:EE:02", "test-other", false);
        unrelated
            .endpoints
            .write()
            .unwrap()
            .insert(Endpoint::Tx, CLASSIC_WEVIBE_TX.into());
        unrelated
            .write_modes
            .write()
            .unwrap()
            .insert(CLASSIC_WEVIBE_TX.into(), true);
        assert!(classic_wevibe_stop_write(&unrelated).is_none());
        session
            .write_modes
            .write()
            .unwrap()
            .insert(CLASSIC_WEVIBE_TX.into(), false);
        assert!(!classic_wevibe_stop_write(&session).unwrap().response);
    }

    #[test]
    fn suppresses_only_classic_wevibe_initialisation_pulse() {
        let pulse = [15, 3, 0, 153, 0, 3, 0, 0];
        let uuid = "f000c000-0451-4000-b000-000000000000";
        assert_eq!(
            safe_initial_write(true, true, uuid, &pulse),
            vec![15, 0, 0, 0, 0, 0, 0, 0]
        );
        assert_eq!(safe_initial_write(false, true, uuid, &pulse), pulse);
        assert_eq!(safe_initial_write(true, false, uuid, &pulse), pulse);
        assert_eq!(safe_initial_write(true, true, "other", &pulse), pulse);
    }

    #[tokio::test]
    async fn driver_stop_is_captured_without_starting_gatt_or_enabling_controls() {
        let bridge = Bridge::new(Arc::new(
            buttplug_server_device_config::DeviceConfigurationManager::default(),
        ));
        bridge.enabled.store(true, Ordering::SeqCst);
        let session = Arc::new(Session::new("AA:BB:CC:DD:EE:01", "capture", false));
        session
            .endpoints
            .write()
            .unwrap()
            .insert(Endpoint::Tx, CLASSIC_WEVIBE_TX.into());
        *session.stop_recording.lock().unwrap() = Some(Vec::new());
        bridge.register(session.clone());
        let hardware = HaHardware {
            bridge,
            session: session.clone(),
        };
        // Arbitrary bytes test capture fidelity, not a guessed device protocol.
        let command = HardwareWriteCmd::new(&[], Endpoint::Tx, vec![7, 0, 9], true);
        hardware.write_value(&command).await.unwrap();
        let recording = session.stop_recording.lock().unwrap();
        assert_eq!(recording.as_ref().unwrap()[0].data, vec![7, 0, 9]);
        assert_eq!(session.inflight.load(Ordering::SeqCst), 0);
        assert!(!session.ready.load(Ordering::SeqCst));
    }
}
