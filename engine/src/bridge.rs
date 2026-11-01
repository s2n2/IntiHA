//! Bounded, generation-scoped RPC channel to Home Assistant.
use std::{
    collections::{HashMap, HashSet},
    sync::{
        Arc, Mutex, RwLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use buttplug_core::errors::ButtplugDeviceError;
use buttplug_server::device::hardware::{
    HardwareEvent, communication::HardwareCommunicationManagerEvent,
};
use buttplug_server_device_config::{
    BluetoothLESpecifier, DeviceConfigurationManager, Endpoint, ProtocolCommunicationSpecifier,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::sync::{broadcast, mpsc, oneshot};
use uuid::Uuid;

pub type DeviceResult<T> = Result<T, ButtplugDeviceError>;
pub fn device_error(message: impl Into<String>) -> ButtplugDeviceError {
    ButtplugDeviceError::DeviceCommunicationError(message.into())
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Advertisement {
    pub address: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub service_uuids: Vec<Uuid>,
    #[serde(default)]
    pub manufacturer_data: HashMap<u16, Vec<u8>>,
    pub rssi: Option<i16>,
    pub source: Option<String>,
}

impl Advertisement {
    pub fn specifier(&self) -> ProtocolCommunicationSpecifier {
        ProtocolCommunicationSpecifier::BluetoothLE(BluetoothLESpecifier::new_from_device(
            &self.name,
            &self.manufacturer_data,
            &self.service_uuids,
        ))
    }
}

struct Candidate {
    advertisement: Advertisement,
    seen: Instant,
    next_attempt: Instant,
    attempts: u32,
    attempted: bool,
    announced: bool,
    protocols: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StopWrite {
    pub characteristic: String,
    pub data: Vec<u8>,
    pub response: bool,
}

pub struct Session {
    pub id: String,
    pub address: String,
    pub events: broadcast::Sender<HardwareEvent>,
    pub connected: AtomicBool,
    pub ready: AtomicBool,
    pub first_write: AtomicBool,
    pub inflight: AtomicU64,
    pub io_lock: tokio::sync::Mutex<()>,
    pub last_io_error: Mutex<Option<String>>,
    pub classic_wevibe: bool,
    pub endpoints: RwLock<HashMap<Endpoint, String>>,
    pub write_modes: RwLock<HashMap<String, bool>>,
    pub stop_recording: Mutex<Option<Vec<StopWrite>>>,
}

impl Session {
    pub fn new(address: &str, id: &str, classic_wevibe: bool) -> Self {
        let (events, _) = broadcast::channel(64);
        Self {
            id: id.to_owned(),
            address: address.to_owned(),
            events,
            connected: AtomicBool::new(true),
            ready: AtomicBool::new(false),
            first_write: AtomicBool::new(true),
            inflight: AtomicU64::new(0),
            io_lock: tokio::sync::Mutex::new(()),
            last_io_error: Mutex::new(None),
            classic_wevibe,
            endpoints: RwLock::new(HashMap::new()),
            write_modes: RwLock::new(HashMap::new()),
            stop_recording: Mutex::new(None),
        }
    }
}

type Pending = (u64, oneshot::Sender<Result<Value, String>>);
struct PendingGuard<'a> {
    bridge: &'a Bridge,
    id: u64,
}
impl Drop for PendingGuard<'_> {
    fn drop(&mut self) {
        self.bridge.pending.lock().unwrap().remove(&self.id);
    }
}

/// Release the in-progress address even when a connect future is cancelled.
pub struct ConnectionAttempt {
    bridge: Arc<Bridge>,
    address: String,
}
impl Drop for ConnectionAttempt {
    fn drop(&mut self) {
        self.bridge.connecting.lock().unwrap().remove(&self.address);
    }
}

/// A new WebSocket gets a new epoch. Old replies cannot complete new RPCs.
pub struct Bridge {
    writer: Mutex<Option<(u64, mpsc::Sender<Value>)>>,
    epoch: AtomicU64,
    next_id: AtomicU64,
    pending: Mutex<HashMap<u64, Pending>>,
    pub enabled: AtomicBool,
    pub auto_reconnect: AtomicBool,
    pub external_active: AtomicBool,
    pub control_epoch: AtomicU64,
    pub scanning: AtomicBool,
    pub reconnect_paused: AtomicBool,
    candidates: Mutex<HashMap<String, Candidate>>,
    sessions: Mutex<HashMap<String, Arc<Session>>>,
    connecting: Mutex<HashSet<String>>,
    comm_sender: Mutex<Option<mpsc::Sender<HardwareCommunicationManagerEvent>>>,
    configuration: Arc<DeviceConfigurationManager>,
    pub manual_timeout: AtomicU64,
    pub manual_deadline: Mutex<Option<Instant>>,
    pub scan_deadline: Mutex<Option<Instant>>,
    pub last_error: Mutex<Option<String>>,
}

impl Bridge {
    pub fn new(configuration: Arc<DeviceConfigurationManager>) -> Arc<Self> {
        Arc::new(Self {
            writer: Mutex::new(None),
            epoch: AtomicU64::new(0),
            next_id: AtomicU64::new(1),
            pending: Mutex::new(HashMap::new()),
            enabled: AtomicBool::new(false),
            auto_reconnect: AtomicBool::new(true),
            external_active: AtomicBool::new(false),
            control_epoch: AtomicU64::new(0),
            scanning: AtomicBool::new(false),
            reconnect_paused: AtomicBool::new(false),
            candidates: Mutex::new(HashMap::new()),
            sessions: Mutex::new(HashMap::new()),
            connecting: Mutex::new(HashSet::new()),
            comm_sender: Mutex::new(None),
            configuration,
            manual_timeout: AtomicU64::new(60),
            manual_deadline: Mutex::new(None),
            scan_deadline: Mutex::new(None),
            last_error: Mutex::new(None),
        })
    }

    pub fn attach(&self, writer: mpsc::Sender<Value>) -> u64 {
        let epoch = self.epoch.fetch_add(1, Ordering::SeqCst) + 1;
        *self.writer.lock().unwrap() = Some((epoch, writer));
        epoch
    }

    pub fn is_connected(&self) -> bool {
        self.writer.lock().unwrap().is_some()
    }

    pub fn detach(&self, epoch: u64) {
        let mut writer = self.writer.lock().unwrap();
        if writer.as_ref().map(|v| v.0) != Some(epoch) {
            return;
        }
        *writer = None;
        drop(writer);
        self.enabled.store(false, Ordering::SeqCst);
        self.control_epoch.fetch_add(1, Ordering::SeqCst);
        self.scanning.store(false, Ordering::SeqCst);
        self.reconnect_paused.store(false, Ordering::SeqCst);
        *self.manual_deadline.lock().unwrap() = None;
        *self.scan_deadline.lock().unwrap() = None;
        for (_, (_, tx)) in self.pending.lock().unwrap().drain() {
            let _ = tx.send(Err("Home Assistant bridge disconnected".into()));
        }
        let sessions: Vec<_> = self.sessions.lock().unwrap().values().cloned().collect();
        for session in sessions {
            self.invalidate(&session.id);
        }
        self.candidates.lock().unwrap().clear();
    }

    pub fn status(&self) -> Value {
        json!({"type":"status", "version":crate::VERSION,
            "enabled":self.enabled.load(Ordering::SeqCst),
            "bridge_connected":self.is_connected(),
            "client_connected":self.external_active.load(Ordering::SeqCst),
            "connected_clients":if self.external_active.load(Ordering::SeqCst) {1} else {0},
            "scanning":self.scanning.load(Ordering::SeqCst),
            "last_error":self.last_error.lock().unwrap().clone()})
    }

    pub async fn send(&self, value: Value) -> DeviceResult<()> {
        let writer = self
            .writer
            .lock()
            .unwrap()
            .as_ref()
            .map(|v| v.1.clone())
            .ok_or_else(|| device_error("Home Assistant bridge is offline"))?;
        tokio::time::timeout(Duration::from_secs(1), writer.send(value))
            .await
            .map_err(|_| device_error("Home Assistant bridge queue is full"))?
            .map_err(|_| device_error("Home Assistant bridge is offline"))
    }

    pub async fn rpc(&self, mut value: Value, timeout: Duration) -> DeviceResult<Value> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let operation = value["op"].as_str().unwrap_or("unknown").to_owned();
        // Pin transport and epoch together; this RPC may never migrate to a
        // newly attached bridge if the old socket disappears while it awaits.
        let (epoch, writer) = self
            .writer
            .lock()
            .unwrap()
            .clone()
            .ok_or_else(|| device_error("Home Assistant bridge is offline"))?;
        let (tx, rx) = oneshot::channel();
        {
            let mut pending = self.pending.lock().unwrap();
            // Reserve room for stop/disconnect requests even when normal GATT
            // work fills its limit. A stop must not lose a slot to old output.
            let limit = if value["op"] == "disconnect" { 64 } else { 32 };
            if pending.len() >= limit {
                return Err(device_error("Bridge operation limit reached"));
            }
            pending.insert(id, (epoch, tx));
        }
        let _guard = PendingGuard { bridge: self, id };
        value["type"] = "request".into();
        value["id"] = id.into();
        tokio::time::timeout(Duration::from_secs(1), writer.send(value))
            .await
            .map_err(|_| device_error("Home Assistant bridge queue is full"))?
            .map_err(|_| device_error("Home Assistant bridge is offline"))?;
        let result = tokio::time::timeout(timeout, rx).await;
        if self.writer.lock().unwrap().as_ref().map(|v| v.0) != Some(epoch) {
            return Err(device_error("Home Assistant bridge generation changed"));
        }
        match result {
            Ok(Ok(Ok(value))) => Ok(value),
            Ok(Ok(Err(error))) => Err(device_error(if error.trim().is_empty() {
                format!("Home Assistant Bluetooth {operation} failed without error details")
            } else {
                error
            })),
            Ok(Err(_)) => Err(device_error("Bridge operation was cancelled")),
            Err(_) => Err(device_error(format!(
                "Home Assistant Bluetooth {operation} timed out after {} seconds",
                timeout.as_secs()
            ))),
        }
    }

    pub fn response(&self, epoch: u64, value: &Value) {
        let Some(id) = value["id"].as_u64() else {
            return;
        };
        let mut pending = self.pending.lock().unwrap();
        if pending.get(&id).map(|p| p.0) != Some(epoch) {
            return;
        }
        if let Some((_, tx)) = pending.remove(&id) {
            let result = if value["ok"].as_bool() == Some(true) {
                Ok(value.get("result").cloned().unwrap_or(json!({})))
            } else {
                Err(value["error"]
                    .as_str()
                    .unwrap_or("Bluetooth operation failed")
                    .to_owned())
            };
            let _ = tx.send(result);
        }
    }

    pub fn configure(&self, value: &Value) -> Result<(), String> {
        if let Some(value) = value.get("auto_reconnect") {
            self.auto_reconnect.store(
                value.as_bool().ok_or("auto_reconnect must be boolean")?,
                Ordering::SeqCst,
            );
        }
        if let Some(value) = value.get("manual_timeout_seconds") {
            let seconds = value.as_u64().ok_or("Invalid manual timeout")?;
            if !(15..=600).contains(&seconds) {
                return Err("Manual timeout must be 15–600 seconds".into());
            }
            self.manual_timeout.store(seconds, Ordering::SeqCst);
        }
        if let Some(value) = value.get("enabled") {
            let enabled = value.as_bool().ok_or("enabled must be boolean")?;
            if !enabled {
                self.control_epoch.fetch_add(1, Ordering::SeqCst);
            }
            self.enabled.store(enabled, Ordering::SeqCst);
        }
        for candidate in self.candidates.lock().unwrap().values_mut() {
            candidate.next_attempt = Instant::now();
            candidate.attempted = false;
        }
        Ok(())
    }

    pub fn begin_connection(self: &Arc<Self>, address: &str) -> Option<ConnectionAttempt> {
        if !self.connecting.lock().unwrap().insert(address.to_owned()) {
            return None;
        }
        Some(ConnectionAttempt {
            bridge: self.clone(),
            address: address.to_owned(),
        })
    }

    pub async fn device_status(
        &self,
        address: &str,
        connection_id: &str,
        phase: &str,
        error: Option<&str>,
    ) {
        tracing::info!(address, connection_id, phase, error = ?error, "IntiHA device connection stage");
        let _ = self
            .send(json!({
                "type":"device_status", "address":address, "connection_id":connection_id,
                "phase":phase, "error":error
            }))
            .await;
    }

    pub fn supported(&self, address: &str) -> bool {
        self.candidates
            .lock()
            .unwrap()
            .get(address)
            .is_some_and(|candidate| candidate.announced)
    }

    pub async fn advertisement(&self, value: Value) {
        let Ok(mut advertisement) = serde_json::from_value::<Advertisement>(value) else {
            return;
        };
        if !valid_address(&advertisement.address)
            || advertisement.name.len() > 248
            || advertisement.service_uuids.len() > 64
            || advertisement.manufacturer_data.len() > 64
            || advertisement
                .manufacturer_data
                .values()
                .any(|v| v.len() > 512)
        {
            return;
        }
        advertisement.address = advertisement.address.to_uppercase();
        let specifier = advertisement.specifier();
        let protocols: Vec<String> = self
            .configuration
            .base_communication_specifiers()
            .iter()
            .filter(|(_, specifiers)| specifiers.contains(&specifier))
            .map(|(name, _)| name.clone())
            .collect();
        if protocols.is_empty() {
            return;
        }
        let notify = {
            let mut candidates = self.candidates.lock().unwrap();
            if candidates.len() >= 256 && !candidates.contains_key(&advertisement.address) {
                return;
            }
            let candidate = candidates
                .entry(advertisement.address.clone())
                .or_insert_with(|| Candidate {
                    advertisement: advertisement.clone(),
                    seen: Instant::now(),
                    next_attempt: Instant::now(),
                    attempts: 0,
                    attempted: false,
                    announced: false,
                    protocols: protocols.clone(),
                });
            candidate.advertisement = advertisement.clone();
            candidate.seen = Instant::now();
            candidate.protocols = protocols.clone();
            !candidate.announced
        };
        if notify || self.scanning.load(Ordering::SeqCst) {
            let sent = self.send(json!({"type":"discovered", "address":advertisement.address,
                "name":advertisement.name,"protocols":protocols,"rssi":advertisement.rssi,"source":advertisement.source})).await;
            if sent.is_ok()
                && let Some(candidate) = self
                    .candidates
                    .lock()
                    .unwrap()
                    .get_mut(&advertisement.address)
            {
                // Admission at HA follows this event. Do not issue connect
                // before it is queued, including when discovery races a tick.
                candidate.announced = true;
            }
        }
    }

    pub fn install_manager(&self, sender: mpsc::Sender<HardwareCommunicationManagerEvent>) {
        *self.comm_sender.lock().unwrap() = Some(sender);
    }

    pub async fn discover_due(self: &Arc<Self>) {
        if self.reconnect_paused.load(Ordering::SeqCst)
            || !self.enabled.load(Ordering::SeqCst)
            || !self.is_connected()
        {
            return;
        }
        let Some(sender) = self.comm_sender.lock().unwrap().clone() else {
            return;
        };
        let connected: HashSet<_> = self
            .sessions
            .lock()
            .unwrap()
            .values()
            .map(|s| s.address.clone())
            .collect();
        let connecting = self.connecting.lock().unwrap().clone();
        let now = Instant::now();
        let due: Vec<_> = {
            let mut candidates = self.candidates.lock().unwrap();
            candidates.retain(|address, candidate| {
                // Keep attempted addresses so auto_reconnect=false continues
                // to mean no automatic retry after a long absence.
                candidate.attempted
                    || connected.contains(address)
                    || now.duration_since(candidate.seen).as_secs() < 180
            });
            candidates
                .values_mut()
                .filter_map(|candidate| {
                    if !candidate.announced
                        || connected.contains(&candidate.advertisement.address)
                        || connecting.contains(&candidate.advertisement.address)
                        || now.duration_since(candidate.seen) > Duration::from_secs(120)
                        || candidate.next_attempt > now
                        || (candidate.attempted
                            && !self.auto_reconnect.load(Ordering::SeqCst)
                            && !self.scanning.load(Ordering::SeqCst))
                    {
                        return None;
                    }
                    candidate.attempted = true;
                    candidate.next_attempt = now + Duration::from_secs(60);
                    Some((candidate.advertisement.clone(), candidate.protocols.clone()))
                })
                .take(4)
                .collect()
        };
        for (advertisement, protocols) in due {
            let event = HardwareCommunicationManagerEvent::DeviceFound {
                name: advertisement.name.clone(),
                address: advertisement.address.clone(),
                creator: Box::new(crate::hardware::HaConnector::new(
                    self.clone(),
                    advertisement,
                    protocols,
                )),
            };
            if sender.send(event).await.is_err() {
                break;
            }
        }
    }

    pub fn failed_attempt(&self, address: &str) {
        if let Some(candidate) = self.candidates.lock().unwrap().get_mut(address) {
            candidate.attempts = (candidate.attempts + 1).min(6);
            // Small UUID-derived jitter avoids simultaneous proxy reconnect storms.
            let jitter = u64::from(Uuid::new_v4().as_bytes()[0]) * 3;
            candidate.next_attempt = Instant::now()
                + Duration::from_millis((1u64 << candidate.attempts).min(60) * 1000 + jitter);
        }
    }

    pub fn mark_ready(&self, session: &Arc<Session>) -> bool {
        // Only a fully initialised current connection resets failure backoff.
        // Keeping the session map locked prevents a concurrent disconnect from
        // being followed by a late ready flag for the retired generation.
        let sessions = self.sessions.lock().unwrap();
        if !sessions
            .get(&session.id)
            .is_some_and(|current| Arc::ptr_eq(current, session))
            || !session.connected.load(Ordering::SeqCst)
        {
            return false;
        }
        session.ready.store(true, Ordering::SeqCst);
        if let Some(candidate) = self.candidates.lock().unwrap().get_mut(&session.address) {
            candidate.attempts = 0;
        }
        true
    }

    pub fn register(&self, session: Arc<Session>) {
        self.sessions
            .lock()
            .unwrap()
            .insert(session.id.clone(), session);
    }
    pub fn session(&self, id: &str) -> Option<Arc<Session>> {
        self.sessions.lock().unwrap().get(id).cloned()
    }
    pub fn all_sessions(&self) -> Vec<Arc<Session>> {
        self.sessions.lock().unwrap().values().cloned().collect()
    }
    pub fn session_for(&self, address: &str) -> Option<Arc<Session>> {
        self.sessions
            .lock()
            .unwrap()
            .values()
            .find(|s| s.address == address)
            .cloned()
    }

    pub fn invalidate(&self, id: &str) {
        let session = self.sessions.lock().unwrap().remove(id);
        if let Some(session) = session {
            session.connected.store(false, Ordering::SeqCst);
            let was_ready = session.ready.swap(false, Ordering::SeqCst);
            if was_ready && self.external_active.load(Ordering::SeqCst) {
                // The public protocol has only reusable device indexes. Close
                // its owner when a ready session disappears, so buffered old
                // commands cannot reach a new connection under the same index.
                self.control_epoch.fetch_add(1, Ordering::SeqCst);
            }
            let _ = session
                .events
                .send(HardwareEvent::Disconnected(session.address.clone()));
            self.failed_attempt(&session.address);
        }
    }

    pub async fn disconnect_session(&self, session: &Arc<Session>) -> DeviceResult<()> {
        self.invalidate(&session.id);
        let result = self
            .rpc(
                json!({"op":"disconnect","address":session.address,"connection_id":session.id}),
                Duration::from_secs(12),
            )
            .await;
        let _ = self
            .send(json!({"type":"output_clear","address":session.address}))
            .await;
        result.map(|_| ())
    }

    pub async fn disconnect_if_disabled(&self) {
        if self.enabled.load(Ordering::SeqCst) {
            return;
        }
        let sessions: Vec<_> = self.sessions.lock().unwrap().values().cloned().collect();
        futures::future::join_all(
            sessions
                .iter()
                .map(|session| self.disconnect_session(session)),
        )
        .await;
    }

    pub fn device_event(&self, value: &Value) {
        let Some(id) = value["connection_id"].as_str() else {
            return;
        };
        let Some(session) = self.session(id) else {
            return;
        };
        if value["address"].as_str() != Some(&session.address) {
            return;
        }
        if value["type"] == "disconnected" {
            self.invalidate(id);
            return;
        }
        if value["type"] != "notification" {
            return;
        }
        let Some(characteristic) = value["characteristic"].as_str() else {
            return;
        };
        let Ok(data) = serde_json::from_value::<Vec<u8>>(value["data"].clone()) else {
            return;
        };
        if data.len() > 512 {
            return;
        }
        let endpoint = session
            .endpoints
            .read()
            .unwrap()
            .iter()
            .find(|(_, uuid)| uuid.eq_ignore_ascii_case(characteristic))
            .map(|(ep, _)| *ep);
        if let Some(endpoint) = endpoint {
            let _ = session.events.send(HardwareEvent::Notification(
                session.address.clone(),
                endpoint,
                data,
            ));
        }
    }

    pub async fn scan(&self, enabled: bool) -> DeviceResult<()> {
        self.rpc(
            json!({"op":"scan","enabled":enabled}),
            Duration::from_secs(12),
        )
        .await?;
        self.scanning.store(enabled, Ordering::SeqCst);
        if enabled {
            for candidate in self.candidates.lock().unwrap().values_mut() {
                candidate.next_attempt = Instant::now();
                candidate.attempted = false;
            }
        }
        Ok(())
    }

    pub async fn mark_scanning_finished(&self) {
        self.scanning.store(false, Ordering::SeqCst);
        if let Some(sender) = self.comm_sender.lock().unwrap().clone() {
            let _ = sender.try_send(HardwareCommunicationManagerEvent::ScanningFinished);
        }
    }
}

pub fn valid_address(address: &str) -> bool {
    let parts: Vec<_> = address.split(':').collect();
    parts.len() == 6
        && parts
            .iter()
            .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_mac_addresses() {
        assert!(valid_address("AA:bb:00:11:22:33"));
        assert!(!valid_address("AA:BB:CC"));
        assert!(!valid_address("GG:00:00:00:00:00"));
    }
    #[tokio::test]
    async fn old_epoch_cannot_complete_new_rpc() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        let (tx, mut rx) = mpsc::channel(4);
        let epoch = bridge.attach(tx);
        let b = bridge.clone();
        let task =
            tokio::spawn(async move { b.rpc(json!({"op":"scan"}), Duration::from_secs(1)).await });
        let request = rx.recv().await.unwrap();
        bridge.response(
            epoch - 1,
            &json!({"id":request["id"],"ok":true,"result":{"wrong":true}}),
        );
        assert!(!task.is_finished());
        bridge.response(
            epoch,
            &json!({"id":request["id"],"ok":true,"result":{"right":true}}),
        );
        assert_eq!(task.await.unwrap().unwrap()["right"], true);
    }
    #[tokio::test]
    async fn disconnect_drains_pending_and_stops_state() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        let (tx, mut rx) = mpsc::channel(4);
        let epoch = bridge.attach(tx);
        bridge.enabled.store(true, Ordering::SeqCst);
        let b = bridge.clone();
        let task =
            tokio::spawn(async move { b.rpc(json!({"op":"read"}), Duration::from_secs(30)).await });
        rx.recv().await.unwrap();
        bridge.detach(epoch);
        assert!(task.await.unwrap().is_err());
        assert!(!bridge.enabled.load(Ordering::SeqCst));
        assert!(bridge.pending.lock().unwrap().is_empty());
    }
    #[test]
    fn stale_disconnect_cannot_remove_new_connection() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        bridge.register(Arc::new(Session::new("AA:BB:CC:DD:EE:FF", "new", false)));
        bridge.device_event(
            &json!({"type":"disconnected","address":"AA:BB:CC:DD:EE:FF","connection_id":"old"}),
        );
        assert!(bridge.session("new").is_some());
    }

    #[test]
    fn ready_session_loss_invalidates_external_owner_once() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        bridge.external_active.store(true, Ordering::SeqCst);
        let session = Arc::new(Session::new("AA:BB:CC:DD:EE:FF", "ready", false));
        session.ready.store(true, Ordering::SeqCst);
        bridge.register(session);
        let before = bridge.control_epoch.load(Ordering::SeqCst);
        bridge.invalidate("ready");
        assert_eq!(bridge.control_epoch.load(Ordering::SeqCst), before + 1);
        bridge.invalidate("ready");
        assert_eq!(bridge.control_epoch.load(Ordering::SeqCst), before + 1);

        bridge.register(Arc::new(Session::new(
            "AA:BB:CC:DD:EE:FF",
            "preparing",
            false,
        )));
        bridge.invalidate("preparing");
        assert_eq!(bridge.control_epoch.load(Ordering::SeqCst), before + 1);
    }

    fn discovery_bridge() -> Arc<Bridge> {
        Bridge::new(Arc::new(
            buttplug_server_device_config::load_protocol_configs(&None, &None, false)
                .unwrap()
                .finish()
                .unwrap(),
        ))
    }

    fn known_advertisement() -> Value {
        json!({"address":"AA:BB:CC:DD:EE:01","name":"Rave","service_uuids":[],"manufacturer_data":{}})
    }

    #[tokio::test]
    async fn continuous_discovery_ignores_unsupported_devices_and_respects_off() {
        let bridge = discovery_bridge();
        let (tx, mut rx) = mpsc::channel(4);
        bridge.attach(tx);
        let (manager, mut found) = mpsc::channel(4);
        bridge.install_manager(manager);
        bridge.advertisement(json!({"address":"AA:BB:CC:DD:EE:02","name":"ordinary temperature sensor","service_uuids":[],"manufacturer_data":{}})).await;
        assert!(rx.try_recv().is_err());
        assert!(bridge.candidates.lock().unwrap().is_empty());

        bridge.advertisement(known_advertisement()).await;
        assert_eq!(rx.recv().await.unwrap()["type"], "discovered");
        bridge.discover_due().await;
        assert!(
            found.try_recv().is_err(),
            "Master off must block connections"
        );
        bridge.configure(&json!({"enabled":true})).unwrap();
        bridge.discover_due().await;
        assert!(
            matches!(found.try_recv().unwrap(), HardwareCommunicationManagerEvent::DeviceFound { address, .. } if address == "AA:BB:CC:DD:EE:01")
        );
    }

    #[tokio::test]
    async fn connect_waits_until_discovery_is_queued_for_home_assistant() {
        let bridge = discovery_bridge();
        let (tx, mut rx) = mpsc::channel(1);
        tx.send(json!({"type":"queue_filler"})).await.unwrap();
        bridge.attach(tx);
        bridge.configure(&json!({"enabled":true})).unwrap();
        let (manager, mut found) = mpsc::channel(4);
        bridge.install_manager(manager);
        let b = bridge.clone();
        let announcement = tokio::spawn(async move {
            b.advertisement(known_advertisement()).await;
        });
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if !bridge.candidates.lock().unwrap().is_empty() {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        bridge.discover_due().await;
        assert!(found.try_recv().is_err());
        assert!(!bridge.supported("AA:BB:CC:DD:EE:01"));
        assert_eq!(rx.recv().await.unwrap()["type"], "queue_filler");
        announcement.await.unwrap();
        assert_eq!(rx.recv().await.unwrap()["type"], "discovered");
        bridge.discover_due().await;
        assert!(found.try_recv().is_ok());
    }

    #[tokio::test]
    async fn auto_reconnect_off_waits_for_explicit_search_even_after_long_absence() {
        let bridge = discovery_bridge();
        let (tx, mut rx) = mpsc::channel(4);
        let epoch = bridge.attach(tx);
        let (manager, mut found) = mpsc::channel(4);
        bridge.install_manager(manager);
        bridge
            .configure(&json!({"enabled":true,"auto_reconnect":false}))
            .unwrap();
        bridge.advertisement(known_advertisement()).await;
        rx.recv().await.unwrap();
        bridge.discover_due().await;
        assert!(found.try_recv().is_ok());
        {
            let mut candidates = bridge.candidates.lock().unwrap();
            let candidate = candidates.get_mut("AA:BB:CC:DD:EE:01").unwrap();
            candidate.next_attempt = Instant::now();
            candidate.seen = Instant::now() - Duration::from_secs(300);
        }
        bridge.discover_due().await;
        assert_eq!(bridge.candidates.lock().unwrap().len(), 1);
        bridge.advertisement(known_advertisement()).await;
        bridge.discover_due().await;
        assert!(found.try_recv().is_err());

        let b = bridge.clone();
        let search = tokio::spawn(async move { b.scan(true).await });
        let request = rx.recv().await.unwrap();
        assert_eq!(request["op"], "scan");
        bridge.response(epoch, &json!({"id":request["id"],"ok":true,"result":{}}));
        search.await.unwrap().unwrap();
        bridge.discover_due().await;
        assert!(found.try_recv().is_ok());
    }

    #[tokio::test]
    async fn a_ready_connection_resets_backoff_for_the_next_roam() {
        let bridge = discovery_bridge();
        let (tx, _rx) = mpsc::channel(4);
        bridge.attach(tx);
        bridge.advertisement(known_advertisement()).await;
        let address = "AA:BB:CC:DD:EE:01";
        for _ in 0..6 {
            bridge.failed_attempt(address);
        }
        assert_eq!(bridge.candidates.lock().unwrap()[address].attempts, 6);
        let session = Arc::new(Session::new(address, "ready-session", false));
        bridge.register(session.clone());
        assert!(bridge.mark_ready(&session));
        assert_eq!(bridge.candidates.lock().unwrap()[address].attempts, 0);
        assert!(session.ready.load(Ordering::SeqCst));

        bridge.invalidate(&session.id);
        {
            let candidates = bridge.candidates.lock().unwrap();
            let candidate = &candidates[address];
            assert_eq!(candidate.attempts, 1);
            assert!(candidate.next_attempt > Instant::now());
            assert!(candidate.next_attempt < Instant::now() + Duration::from_secs(3));
        }
        assert!(
            !bridge.mark_ready(&session),
            "Retired sessions cannot become ready again"
        );
        assert!(!session.ready.load(Ordering::SeqCst));
        assert_eq!(bridge.candidates.lock().unwrap()[address].attempts, 1);
    }

    #[test]
    fn connect_attempt_guard_prevents_overlap_and_releases_on_drop() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        let address = "AA:BB:CC:DD:EE:01";
        let attempt = bridge.begin_connection(address).unwrap();
        assert!(bridge.begin_connection(address).is_none());
        drop(attempt);
        assert!(bridge.begin_connection(address).is_some());
    }

    #[tokio::test]
    async fn rpc_timeout_reports_operation_instead_of_empty_error() {
        let bridge = Bridge::new(Arc::new(DeviceConfigurationManager::default()));
        let (tx, _rx) = mpsc::channel(4);
        bridge.attach(tx);
        let error = bridge
            .rpc(json!({"op":"prepare_stop"}), Duration::from_millis(1))
            .await
            .unwrap_err();
        assert!(error.to_string().contains("prepare_stop"));
        assert!(bridge.pending.lock().unwrap().is_empty());
    }
}
