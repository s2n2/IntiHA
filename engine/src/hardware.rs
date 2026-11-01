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
    protocols: Vec<String>,
}
impl HaConnector {
    pub fn new(bridge: Arc<Bridge>, advertisement: Advertisement, protocols: Vec<String>) -> Self {
        Self {
            bridge,
            advertisement,
            protocols,
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
        if self.bridge.session_for(address).is_some() {
            return Err(device_error("Device already has a BLE connection"));
        }
        let id = Uuid::new_v4().to_string();
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
        let session = Arc::new(Session::new(
            address,
            &id,
            self.protocols.iter().any(|p| p == "wevibe"),
        ));
        self.bridge.register(session.clone());
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
        for (service_uuid, characteristic_map) in specifier.services() {
            if let Some(service) = self.services.iter().find(|s| s.uuid == *service_uuid) {
                for (endpoint, uuid) in characteristic_map {
                    if service.characteristics.iter().any(|c| c.uuid == *uuid) {
                        endpoints.insert(*endpoint, uuid.to_string());
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
        *self.session.endpoints.write().unwrap() = endpoints;
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
            let _guard = OperationGuard(session.clone());
            let result = bridge.rpc(args, Duration::from_secs(12)).await;
            if result.is_err() {
                // A timed-out write has unknown delivery state. Tear down the session;
                // never replay the command on a fresh connection.
                let _ = bridge.disconnect_session(&session).await;
            }
            result
        }
        .boxed()
    }
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
        && characteristic.eq_ignore_ascii_case("f000c000-0451-4000-b000-000000000000")
        && data == [0x0f, 0x03, 0x00, 0x99, 0x00, 0x03, 0x00, 0x00]
    {
        vec![0x0f, 0, 0, 0, 0, 0, 0, 0]
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
            self.session.classic_wevibe,
            &characteristic,
            message.data(),
        );
        let write = StopWrite {
            characteristic,
            data: data.clone(),
            response: message.write_with_response(),
        };
        let session = self.session.clone();
        let result = self.operation(
            message.endpoint(),
            "write",
            json!({"data":data,"response":message.write_with_response()}),
        );
        async move {
            result.await?;
            if let Some(recording) = session.stop_recording.lock().unwrap().as_mut() {
                recording.push(write);
            }
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
}
