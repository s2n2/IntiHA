//! Authenticated WebSocket front doors and safety/lifecycle supervision.
use crate::{VERSION, bridge::Bridge, hardware::HaManagerBuilder};
use axum::{
    Json, Router,
    extract::{
        Query, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use buttplug_core::message::{
    ButtplugMessage, ButtplugMessageSpecVersion,
    serializer::{ButtplugMessageSerializer, ButtplugSerializedMessage},
};
use buttplug_server::{
    ButtplugServer, ButtplugServerBuilder,
    device::{ServerDeviceManager, ServerDeviceManagerBuilder},
    message::{ButtplugClientMessageVariant, serializer::ButtplugServerJSONSerializer},
};
use buttplug_server_device_config::load_protocol_configs;
use futures::{Sink, SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::sync::{Mutex, Semaphore, mpsc};

#[derive(Clone)]
pub struct Config {
    pub bridge_token: String,
    pub client_token: String,
    pub bind: String,
    pub client_port: u16,
    pub bridge_port: u16,
}
impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        let config = Self {
            bridge_token: std::env::var("INTIHA_BRIDGE_TOKEN").unwrap_or_default(),
            client_token: std::env::var("INTIHA_CLIENT_TOKEN").unwrap_or_default(),
            bind: std::env::var("INTIHA_BIND").unwrap_or_else(|_| "0.0.0.0".into()),
            client_port: std::env::var("INTIHA_CLIENT_PORT")
                .unwrap_or_else(|_| "12345".into())
                .parse()?,
            bridge_port: std::env::var("INTIHA_BRIDGE_PORT")
                .unwrap_or_else(|_| "12346".into())
                .parse()?,
        };
        config.validate()?;
        Ok(config)
    }
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.bridge_token.len() >= 32 && self.client_token.len() >= 32,
            "Both IntiHA tokens must have at least 32 characters"
        );
        anyhow::ensure!(
            self.bridge_token != self.client_token,
            "Bridge and client tokens must be different"
        );
        anyhow::ensure!(
            self.bridge_token.len() <= 256 && self.client_token.len() <= 256,
            "Tokens must be no longer than 256 characters"
        );
        anyhow::ensure!(
            self.bridge_token.bytes().all(|b| b.is_ascii_graphic())
                && self.client_token.bytes().all(|b| b.is_ascii_graphic()),
            "Tokens must contain printable ASCII characters without spaces"
        );
        anyhow::ensure!(
            self.client_port != self.bridge_port || self.client_port == 0,
            "Listeners need different ports"
        );
        Ok(())
    }
}

pub struct App {
    pub config: Config,
    pub bridge: Arc<Bridge>,
    manager: Arc<ServerDeviceManager>,
    admin_server: Arc<ButtplugServer>,
    admin_serializer: ButtplugServerJSONSerializer,
    admin_lock: Mutex<()>,
    bridge_slot: Arc<Semaphore>,
    client_slot: Arc<Semaphore>,
    last_inventory: Mutex<Value>,
    stop_lock: Mutex<()>,
    stopping: AtomicBool,
}
struct ReconnectPause<'a>(&'a AtomicBool);
impl Drop for ReconnectPause<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

impl App {
    pub async fn new(config: Config) -> anyhow::Result<Arc<Self>> {
        config.validate()?;
        let configuration = Arc::new(load_protocol_configs(&None, &None, false)?.finish()?);
        let bridge = Bridge::new(configuration.clone());
        let mut manager_builder = ServerDeviceManagerBuilder::new_with_arc(configuration);
        manager_builder.comm_manager(HaManagerBuilder(bridge.clone()));
        let manager = Arc::new(manager_builder.finish()?);
        let admin_server = Arc::new(
            ButtplugServerBuilder::with_shared_device_manager(manager.clone())
                .name("IntiHA management")
                .finish()?,
        );
        let serializer = ButtplugServerJSONSerializer::default();
        serializer.force_message_version(&ButtplugMessageSpecVersion::Version3);
        let app = Arc::new(Self {
            config,
            bridge,
            manager,
            admin_server,
            admin_serializer: serializer,
            admin_lock: Mutex::new(()),
            bridge_slot: Arc::new(Semaphore::new(1)),
            client_slot: Arc::new(Semaphore::new(1)),
            last_inventory: Mutex::new(Value::Null),
            stop_lock: Mutex::new(()),
            stopping: AtomicBool::new(false),
        });
        app.v3(json!({"RequestServerInfo":{"Id":1,"ClientName":"IntiHA management","MessageVersion":3}})).await.map_err(anyhow::Error::msg)?;
        Ok(app)
    }

    pub fn client_router(self: &Arc<Self>) -> Router {
        Router::new()
            .route("/", get(client_upgrade))
            .route("/buttplug", get(client_upgrade))
            .route("/health", get(health))
            .with_state(self.clone())
    }
    pub fn bridge_router(self: &Arc<Self>) -> Router {
        Router::new()
            .route("/bridge", get(bridge_upgrade))
            .route("/health", get(health))
            .with_state(self.clone())
    }

    async fn v3(&self, message: Value) -> Result<Value, String> {
        let mut messages = self
            .admin_serializer
            .deserialize(&ButtplugSerializedMessage::Text(
                json!([message]).to_string(),
            ))
            .map_err(|_| "Invalid Buttplug v3 command".to_owned())?;
        if messages.len() != 1 {
            return Err("Exactly one command is required".into());
        }
        let response = tokio::time::timeout(
            Duration::from_secs(20),
            self.admin_server.parse_message(messages.remove(0)),
        )
        .await
        .map_err(|_| "Buttplug operation timed out".to_owned())?;
        let response = match response {
            Ok(message) | Err(message) => message,
        };
        let ButtplugSerializedMessage::Text(text) = self.admin_serializer.serialize(&[response])
        else {
            return Err("Unexpected binary response".into());
        };
        let array: Value =
            serde_json::from_str(&text).map_err(|_| "Invalid engine response".to_owned())?;
        let message = array.get(0).ok_or("Empty engine response")?.clone();
        if message.get("Error").is_some() {
            return Err(message["Error"]["ErrorMessage"]
                .as_str()
                .unwrap_or("Buttplug command failed")
                .to_owned());
        }
        Ok(message)
    }

    pub async fn stop_all(&self) -> Result<(), String> {
        let _guard = self.stop_lock.lock().await;
        *self.bridge.manual_deadline.lock().unwrap() = None;
        self.bridge.reconnect_paused.store(true, Ordering::SeqCst);
        let _pause = ReconnectPause(&self.bridge.reconnect_paused);
        let sessions = self.bridge.all_sessions();
        // Invalidate before sending the final cached stop. Old official device
        // queues cannot emit commands into a reconnected physical session.
        let results =
            futures::future::join_all(sessions.iter().map(|s| self.bridge.disconnect_session(s)))
                .await;
        let result = results
            .into_iter()
            .find_map(Result::err)
            .map_or(Ok(()), |e| Err(e.to_string()));
        if let Err(error) = &result {
            *self.bridge.last_error.lock().unwrap() =
                Some(format!("Stop could not be confirmed: {error}"));
            let _ = self.bridge.send(self.bridge.status()).await;
        }
        let _ = self.bridge.send(json!({"type":"output_clear"})).await;
        result
    }

    async fn stop_device(&self, index: u32) -> Result<(), String> {
        let info = self
            .manager
            .device_info(index)
            .ok_or("Device is not connected")?;
        let session = self
            .bridge
            .session_for(info.identifier().address())
            .ok_or("Device has no live Bluetooth session")?;
        self.bridge
            .disconnect_session(&session)
            .await
            .map_err(|e| e.to_string())
    }

    pub async fn administer(&self, value: Value) -> Result<Value, String> {
        let _guard = self.admin_lock.lock().await;
        let op = value["op"].as_str().ok_or("Missing admin operation")?;
        if self.stopping.load(Ordering::SeqCst) && !matches!(op, "stop" | "status") {
            return Err("IntiHA is shutting down".into());
        }
        match op {
            "configure" => {
                if value["enabled"] == true
                    && value.get("_control_epoch").is_some_and(|v| {
                        v.as_u64() != Some(self.bridge.control_epoch.load(Ordering::SeqCst))
                    })
                {
                    return Err("Queued enable was cancelled by stop or off".into());
                }
                self.bridge.configure(&value)?;
                if !self.bridge.enabled.load(Ordering::SeqCst) {
                    let stopped = self.stop_all().await;
                    let _ = self.bridge.scan(false).await;
                    stopped?;
                }
                self.bridge.disconnect_unselected().await;
                let status = self.bridge.status();
                let _ = self.bridge.send(status.clone()).await;
                Ok(status)
            }
            "scan" => {
                if !self.bridge.enabled.load(Ordering::SeqCst) {
                    return Err("Turn IntiHA on before searching".into());
                }
                let seconds = value["seconds"].as_u64().unwrap_or(30).clamp(5, 120);
                self.bridge.scan(true).await.map_err(|e| e.to_string())?;
                *self.bridge.scan_deadline.lock().unwrap() =
                    Some(Instant::now() + Duration::from_secs(seconds));
                Ok(self.bridge.status())
            }
            "stop" => {
                self.stop_all().await?;
                Ok(json!({}))
            }
            "status" => {
                self.refresh_devices(false).await?;
                Ok(self.bridge.status())
            }
            "command" => {
                let message = value
                    .get("message")
                    .ok_or("Missing command message")?
                    .clone();
                let name_owned = one_message_name(&message)?.to_owned();
                let name = name_owned.as_str();
                if !matches!(
                    name,
                    "ScalarCmd"
                        | "VibrateCmd"
                        | "RotateCmd"
                        | "LinearCmd"
                        | "SensorReadCmd"
                        | "SensorSubscribeCmd"
                        | "SensorUnsubscribeCmd"
                        | "BatteryLevelCmd"
                        | "RequestDeviceList"
                        | "StopAllDevices"
                        | "StopDeviceCmd"
                ) {
                    return Err("This management command is not supported".into());
                }
                let movement = is_movement(name);
                if movement {
                    if value.get("_control_epoch").is_some_and(|v| {
                        v.as_u64() != Some(self.bridge.control_epoch.load(Ordering::SeqCst))
                    }) {
                        return Err("Command cancelled by stop or off; it was not replayed".into());
                    }
                    if self.bridge.external_active.load(Ordering::SeqCst) {
                        return Err("An external client owns control".into());
                    }
                    self.check_control_ready(
                        message[name]["DeviceIndex"].as_u64().map(|v| v as u32),
                    )?;
                    let expected_connection = value["connection_id"]
                        .as_str()
                        .ok_or("Movement requires the current connection_id")?;
                    let index = message[name]["DeviceIndex"]
                        .as_u64()
                        .and_then(|index| u32::try_from(index).ok())
                        .ok_or("Movement requires a valid device index")?;
                    let info = self
                        .manager
                        .device_info(index)
                        .ok_or("Device is not connected")?;
                    let session = self
                        .bridge
                        .session_for(info.identifier().address())
                        .ok_or("Device Bluetooth session is unavailable")?;
                    if session.id != expected_connection
                        || !session.connected.load(Ordering::SeqCst)
                    {
                        return Err(
                            "Device connection changed; the queued command was not replayed".into(),
                        );
                    }
                }
                let stopped_index = message[name]["DeviceIndex"].as_u64().map(|v| v as u32);
                if name == "StopAllDevices" {
                    self.stop_all().await?;
                    return Ok(json!({"message":{"Ok":{"Id":message[name]["Id"]}}}));
                }
                if name == "StopDeviceCmd" {
                    self.stop_device(stopped_index.ok_or("Missing device index")?)
                        .await?;
                    return Ok(json!({"message":{"Ok":{"Id":message[name]["Id"]}}}));
                }
                let response = self.v3(message).await?;
                if movement {
                    *self.bridge.manual_deadline.lock().unwrap() = Some(
                        Instant::now()
                            + Duration::from_secs(
                                self.bridge.manual_timeout.load(Ordering::SeqCst),
                            ),
                    );
                } else if name == "StopAllDevices" {
                    *self.bridge.manual_deadline.lock().unwrap() = None;
                    let _ = self.bridge.send(json!({"type":"output_clear"})).await;
                } else if name == "StopDeviceCmd" {
                    // Other manually driven devices still need their timeout.
                    if let Some(info) =
                        stopped_index.and_then(|index| self.manager.device_info(index))
                    {
                        let _ = self.bridge.send(json!({"type":"output_clear","address":info.identifier().address()})).await;
                    }
                }
                Ok(json!({"message":response}))
            }
            _ => Err("Unknown administration operation".into()),
        }
    }

    fn check_control_ready(&self, index: Option<u32>) -> Result<(), String> {
        if self.stopping.load(Ordering::SeqCst)
            || !self.bridge.enabled.load(Ordering::SeqCst)
            || !self.bridge.is_connected()
        {
            return Err("IntiHA is switched off or its Bluetooth bridge is offline".into());
        }
        if let Some(index) = index {
            let info = self
                .manager
                .device_info(index)
                .ok_or("Device is not connected")?;
            let session = self
                .bridge
                .session_for(info.identifier().address())
                .ok_or("Device Bluetooth session is unavailable")?;
            if !session.ready.load(Ordering::SeqCst) {
                return Err("Device is still establishing its safe stopped state".into());
            }
        }
        Ok(())
    }

    async fn refresh_devices(&self, prepare: bool) -> Result<(), String> {
        let list = self.v3(json!({"RequestDeviceList":{"Id":1}})).await?;
        let mut devices = list["DeviceList"]["Devices"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        for device in &mut devices {
            let Some(index) = device["DeviceIndex"].as_u64() else {
                continue;
            };
            let Some(info) = self.manager.device_info(index as u32) else {
                continue;
            };
            let address = info.identifier().address().clone();
            device["address"] = address.clone().into();
            let Some(session) = self.bridge.session_for(&address) else {
                device["ready"] = false.into();
                continue;
            };
            device["connection_id"] = session.id.clone().into();
            if prepare
                && !session.ready.load(Ordering::SeqCst)
                && self.bridge.enabled.load(Ordering::SeqCst)
            {
                *session.stop_recording.lock().unwrap() = Some(Vec::new());
                let stop = self
                    .v3(json!({"StopDeviceCmd":{"Id":1,"DeviceIndex":index}}))
                    .await;
                // The upstream stop waiter deliberately returns Ok after its
                // one-second timeout. Wait for our real GATT operations and a
                // quiet capture window before accepting a complete stop plan.
                let settled = tokio::time::timeout(Duration::from_secs(15), async {
                    loop {
                        if !session.connected.load(Ordering::SeqCst) {
                            return false;
                        }
                        if session.inflight.load(Ordering::SeqCst) == 0 {
                            let count = session
                                .stop_recording
                                .lock()
                                .unwrap()
                                .as_ref()
                                .map_or(0, Vec::len);
                            tokio::time::sleep(Duration::from_millis(150)).await;
                            if session.inflight.load(Ordering::SeqCst) == 0
                                && session
                                    .stop_recording
                                    .lock()
                                    .unwrap()
                                    .as_ref()
                                    .map_or(0, Vec::len)
                                    == count
                            {
                                return true;
                            }
                        } else {
                            tokio::time::sleep(Duration::from_millis(25)).await;
                        }
                    }
                })
                .await
                .unwrap_or(false);
                let writes = session
                    .stop_recording
                    .lock()
                    .unwrap()
                    .take()
                    .unwrap_or_default();
                if stop.is_ok()
                    && settled
                    && session.connected.load(Ordering::SeqCst)
                    && !writes.is_empty()
                    && writes.len() <= 16
                {
                    let plan = self.bridge.rpc(json!({"op":"set_stop_plan","address":address,"connection_id":session.id,"writes":writes}), Duration::from_secs(12)).await;
                    if plan.is_ok() && session.connected.load(Ordering::SeqCst) {
                        session.ready.store(true, Ordering::SeqCst);
                    } else {
                        let _ = self.bridge.disconnect_session(&session).await;
                    }
                } else {
                    *self.bridge.last_error.lock().unwrap() = Some(
                        "A device could not establish a reusable stop command; control was refused"
                            .into(),
                    );
                    let _ = self.bridge.send(self.bridge.status()).await;
                    let _ = self.bridge.disconnect_session(&session).await;
                }
            }
            device["ready"] = session.ready.load(Ordering::SeqCst).into();
        }
        let value = json!({"type":"devices","devices":devices});
        let mut previous = self.last_inventory.lock().await;
        if *previous != value || !prepare {
            *previous = value.clone();
            let _ = self.bridge.send(value).await;
        }
        Ok(())
    }

    /// One supervisor owns retries, manual timeout, discovery timeout, and the
    /// transition from an upstream device to a ready device with a cached stop.
    pub async fn supervise(self: Arc<Self>) {
        tokio::join!(
            self.manual_watchdog(),
            self.discovery_supervisor(),
            self.forward_sensor_events()
        );
    }

    async fn forward_sensor_events(&self) {
        let events = self.admin_server.event_stream();
        futures::pin_mut!(events);
        while let Some(event) = events.next().await {
            let ButtplugSerializedMessage::Text(text) = self.admin_serializer.serialize(&[event])
            else {
                continue;
            };
            if let Ok(array) = serde_json::from_str::<Vec<Value>>(&text) {
                for message in array {
                    if message.get("SensorReading").is_some()
                        || message.get("BatteryLevelReading").is_some()
                    {
                        let _ = self
                            .bridge
                            .send(json!({"type":"sensor","message":message}))
                            .await;
                    }
                }
            }
        }
    }

    async fn manual_watchdog(&self) {
        let mut interval = tokio::time::interval(Duration::from_millis(250));
        loop {
            interval.tick().await;
            let expired = self
                .bridge
                .manual_deadline
                .lock()
                .unwrap()
                .is_some_and(|deadline| deadline <= Instant::now());
            if expired {
                let _guard = self.admin_lock.lock().await;
                if self
                    .bridge
                    .manual_deadline
                    .lock()
                    .unwrap()
                    .is_some_and(|deadline| deadline <= Instant::now())
                {
                    let _ = self.stop_all().await;
                }
            }
        }
    }

    async fn discovery_supervisor(&self) {
        let mut interval = tokio::time::interval(Duration::from_secs(1));
        loop {
            interval.tick().await;
            if !self.bridge.is_connected() {
                continue;
            }
            let scan_expired = self
                .bridge
                .scan_deadline
                .lock()
                .unwrap()
                .is_some_and(|deadline| deadline <= Instant::now());
            if scan_expired {
                *self.bridge.scan_deadline.lock().unwrap() = None;
                let _ = self.bridge.scan(false).await;
                self.bridge.mark_scanning_finished().await;
                let _ = self.bridge.send(self.bridge.status()).await;
            }
            self.bridge.discover_due().await;
            if let Err(error) = self.refresh_devices(true).await {
                *self.bridge.last_error.lock().unwrap() = Some(error);
            }
        }
    }

    pub async fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.bridge.enabled.store(false, Ordering::SeqCst);
        self.bridge.control_epoch.fetch_add(1, Ordering::SeqCst);
        let _guard = self.admin_lock.lock().await;
        let _ = self.stop_all().await;
        self.bridge.disconnect_unselected().await;
        let _ = tokio::time::timeout(Duration::from_secs(15), self.admin_server.shutdown()).await;
    }
}

fn one_message_name(value: &Value) -> Result<&str, String> {
    let object = value.as_object().ok_or("A command must be an object")?;
    if object.len() != 1 {
        return Err("Exactly one message is required".into());
    }
    Ok(object.keys().next().unwrap())
}
fn is_movement(name: &str) -> bool {
    matches!(
        name,
        "ScalarCmd"
            | "VibrateCmd"
            | "SingleMotorVibrateCmd"
            | "RotateCmd"
            | "LinearCmd"
            | "OutputCmd"
            | "FleshlightLaunchFW12Cmd"
            | "VorzeA10CycloneCmd"
            | "KiirooCmd"
            | "LovenseCmd"
    )
}

async fn health() -> Json<Value> {
    Json(json!({"status":"ok","version":VERSION}))
}
fn token_matches(actual: &str, expected: &str) -> bool {
    if actual.len() != expected.len() {
        return false;
    }
    bool::from(actual.as_bytes().ct_eq(expected.as_bytes()))
}
fn bearer(headers: &HeaderMap) -> Option<&str> {
    headers
        .get("authorization")?
        .to_str()
        .ok()?
        .strip_prefix("Bearer ")
}

async fn bridge_upgrade(
    State(app): State<Arc<App>>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if app.stopping.load(Ordering::SeqCst) {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    if !bearer(&headers).is_some_and(|token| token_matches(token, &app.config.bridge_token)) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let Ok(permit) = app.bridge_slot.clone().try_acquire_owned() else {
        return StatusCode::CONFLICT.into_response();
    };
    ws.max_message_size(65536)
        .max_frame_size(65536)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            bridge_connection(app, socket).await;
        })
        .into_response()
}
async fn client_upgrade(
    State(app): State<Arc<App>>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
    ws: WebSocketUpgrade,
) -> Response {
    if app.stopping.load(Ordering::SeqCst) {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    let token = bearer(&headers)
        .or_else(|| query.get("token").map(String::as_str))
        .unwrap_or("");
    if !token_matches(token, &app.config.client_token) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let Ok(permit) = app.client_slot.clone().try_acquire_owned() else {
        return StatusCode::CONFLICT.into_response();
    };
    ws.max_message_size(65536)
        .max_frame_size(65536)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            client_connection(app, socket).await;
        })
        .into_response()
}

async fn bridge_connection(app: Arc<App>, mut socket: WebSocket) {
    let hello = match tokio::time::timeout(Duration::from_secs(10), socket.recv()).await {
        Ok(Some(Ok(Message::Text(text)))) => serde_json::from_str::<Value>(&text).ok(),
        _ => None,
    };
    let Some(hello) = hello.filter(|h| h["type"] == "hello" && h["protocol"] == 1) else {
        let _ = socket.close().await;
        return;
    };
    if app.stopping.load(Ordering::SeqCst) || app.bridge.configure(&hello).is_err() {
        let _ = socket.close().await;
        return;
    }
    let (tx, mut rx) = mpsc::channel::<Value>(64);
    let epoch = app.bridge.attach(tx);
    let (mut writer, mut reader) = socket.split();
    let _ = app
        .bridge
        .send(json!({"type":"hello","protocol":1,"version":VERSION}))
        .await;
    let _ = app.bridge.send(app.bridge.status()).await;
    *app.last_inventory.lock().await = Value::Null;
    // Administrative operations are serial and bounded, while transport replies
    // continue being read. Awaiting an admin RPC in this reader would deadlock.
    let (admin_tx, mut admin_rx) = mpsc::channel::<Value>(8);
    let (urgent_tx, mut urgent_rx) = mpsc::channel::<Value>(4);
    let admin_app = app.clone();
    let admin_task = tokio::spawn(async move {
        loop {
            let request = tokio::select! { biased;
                value = urgent_rx.recv() => value,
                value = admin_rx.recv() => value,
            };
            let Some(request) = request else {
                break;
            };
            let id = request["id"].clone();
            let response = match admin_app.administer(request).await {
                Ok(result) => json!({"type":"admin_result","id":id,"ok":true,"result":result}),
                Err(error) => json!({"type":"admin_result","id":id,"ok":false,"error":error}),
            };
            if admin_app.bridge.send(response).await.is_err() {
                break;
            }
        }
    });
    let mut heartbeat = tokio::time::interval(Duration::from_secs(10));
    let mut last_seen = Instant::now();
    loop {
        tokio::select! {
            message = rx.recv() => {
                let Some(message) = message else { break; };
                if !matches!(tokio::time::timeout(Duration::from_secs(5), writer.send(Message::Text(message.to_string().into()))).await, Ok(Ok(()))) { break; }
            }
            message = reader.next() => {
                let Some(Ok(message)) = message else { break; }; last_seen = Instant::now();
                match message {
                    Message::Text(text) => {
                        let Ok(mut value) = serde_json::from_str::<Value>(&text) else { break; };
                        match value["type"].as_str() {
                            Some("response") => app.bridge.response(epoch, &value),
                            Some("advertisement") => app.bridge.advertisement(value).await,
                            Some("notification"|"disconnected") => { app.bridge.device_event(&value); }
                            Some("admin") => {
                                let urgent = value["op"] == "stop" || (value["op"] == "configure" && value["enabled"] == false)
                                    || (value["op"] == "command" && value["message"].get("StopAllDevices").is_some());
                                if urgent {
                                    // A stop is a barrier at receipt, before it reaches the
                                    // worker. Previously queued output requests are invalid.
                                    app.bridge.control_epoch.fetch_add(1, Ordering::SeqCst);
                                    if value["op"] == "configure" { app.bridge.enabled.store(false, Ordering::SeqCst); }
                                }
                                value["_control_epoch"] = app.bridge.control_epoch.load(Ordering::SeqCst).into();
                                let sender = if urgent { &urgent_tx } else { &admin_tx };
                                if let Err(error) = sender.try_send(value) {
                                    let value = error.into_inner();
                                    let _ = app.bridge.send(json!({"type":"admin_result","id":value["id"],"ok":false,"error":"Administration queue is full"})).await;
                                }
                            }
                            _ => {}
                        }
                    }
                    Message::Ping(payload) => { if !bounded_send(&mut writer, Message::Pong(payload)).await { break; } }
                    Message::Pong(_) => {}
                    Message::Close(_) | Message::Binary(_) => break,
                }
            }
            _ = heartbeat.tick() => {
                if last_seen.elapsed() > Duration::from_secs(30) { break; }
                if !bounded_send(&mut writer, Message::Ping(Vec::new().into())).await { break; }
            }
        }
    }
    app.bridge.detach(epoch);
    admin_task.abort();
    let _ = admin_task.await;
    let _ = app.stop_all().await;
    let _ = tokio::time::timeout(Duration::from_secs(2), writer.close()).await;
}

async fn client_connection(app: Arc<App>, mut socket: WebSocket) {
    // Transfer ownership only after issuing a real stop for any HA manual output.
    let guard = app.admin_lock.lock().await;
    let manual_active = app.bridge.manual_deadline.lock().unwrap().is_some();
    if manual_active {
        let _ = app.stop_all().await;
    }
    app.bridge.external_active.store(true, Ordering::SeqCst);
    let control_epoch = app.bridge.control_epoch.load(Ordering::SeqCst);
    drop(guard);
    let _ = app.bridge.send(app.bridge.status()).await;
    let Ok(server) = ButtplugServerBuilder::with_shared_device_manager(app.manager.clone())
        .name("IntiHA")
        .max_ping_time(20_000)
        .finish()
    else {
        return;
    };
    let serializer = ButtplugServerJSONSerializer::default();
    let events = server.event_stream();
    futures::pin_mut!(events);
    let mut heartbeat = tokio::time::interval(Duration::from_secs(5));
    let started = Instant::now();
    let mut last_seen = Instant::now();
    loop {
        tokio::select! {
            message = socket.recv() => {
                if app.bridge.control_epoch.load(Ordering::SeqCst) != control_epoch { break; }
                let Some(Ok(message)) = message else { break; }; last_seen = Instant::now();
                match message {
                    Message::Text(text) => {
                        let parsed = serializer.deserialize(&ButtplugSerializedMessage::Text(text.to_string()));
                        let Ok(messages) = parsed else {
                            let _ = bounded_send(&mut socket, error_message(0,"Invalid Buttplug message or handshake")).await; break;
                        };
                        if messages.len() > 32 { break; }
                        for message in messages {
                            let _guard = app.admin_lock.lock().await;
                            if app.bridge.control_epoch.load(Ordering::SeqCst) != control_epoch { break; }
                            let id = message.id();
                            if let Err(error) = external_policy(&app, &message) {
                                if !bounded_send(&mut socket, error_message(id,&error)).await { break; }
                                continue;
                            }
                            if server.connected()
                                && let Ok(value) = external_message_value(&message) {
                                    let stop_name = ["StopAllDevices", "StopDeviceCmd", "StopCmd"].into_iter().find(|name| value.get(*name).is_some());
                                    if let Some(name) = stop_name
                                        && (name != "StopCmd" || value[name]["Outputs"].as_bool().unwrap_or(true)) {
                                            let result = if let Some(index) = value[name]["DeviceIndex"].as_u64() {
                                                app.stop_device(index as u32).await
                                            } else { app.stop_all().await };
                                            let response = match result {
                                                Ok(()) => Message::Text(json!([{"Ok":{"Id":id}}]).to_string().into()),
                                                Err(error) => error_message(id,&format!("Stop could not be confirmed: {error}")),
                                            };
                                            if !bounded_send(&mut socket,response).await { break; }
                                            continue;
                                    }
                            }
                            let result = tokio::time::timeout(Duration::from_secs(20), server.parse_message(message)).await;
                            let response = match result {
                                Ok(Ok(response)) | Ok(Err(response)) => serializer.serialize(&[response]),
                                Err(_) => { let _ = app.stop_all().await; let _ = bounded_send(&mut socket, error_message(id,"Device operation timed out; stop attempted, physical state unconfirmed")).await; continue; }
                            };
                            if let ButtplugSerializedMessage::Text(text) = response
                                && !bounded_send(&mut socket, Message::Text(text.into())).await { break; }
                        }
                    }
                    Message::Ping(payload) => { if !bounded_send(&mut socket, Message::Pong(payload)).await { break; } }
                    Message::Pong(_) => {}
                    Message::Close(_)|Message::Binary(_) => break,
                }
            }
            event = events.next() => {
                let Some(event) = event else { break; };
                if server.connected()
                    && let ButtplugSerializedMessage::Text(text) = serializer.serialize(&[event])
                    && !bounded_send(&mut socket, Message::Text(text.into())).await { break; }
            }
            _ = heartbeat.tick() => {
                if app.bridge.control_epoch.load(Ordering::SeqCst) != control_epoch { break; }
                if (!server.connected() && started.elapsed() > Duration::from_secs(10)) || last_seen.elapsed() > Duration::from_secs(30) { break; }
                if !bounded_send(&mut socket, Message::Ping(Vec::new().into())).await { break; }
            }
        }
    }
    let _ = tokio::time::timeout(Duration::from_secs(20), server.disconnect()).await;
    let _ = app.stop_all().await;
    app.bridge.external_active.store(false, Ordering::SeqCst);
    let _ = app.bridge.send(app.bridge.status()).await;
    let _ = tokio::time::timeout(Duration::from_secs(2), socket.close()).await;
}

async fn bounded_send<S: Sink<Message> + Unpin>(socket: &mut S, message: Message) -> bool {
    matches!(
        tokio::time::timeout(Duration::from_secs(5), socket.send(message)).await,
        Ok(Ok(()))
    )
}

fn external_policy(app: &App, message: &ButtplugClientMessageVariant) -> Result<(), String> {
    // Debug's leading variant names are not a security decision: serialize the
    // actual typed message through serde for each public version.
    let value = external_message_value(message)?;
    let name = one_message_name(&value)?;
    if name.starts_with("Raw") {
        return Err("Raw hardware commands are disabled".into());
    }
    if is_movement(name) {
        app.check_control_ready(message.device_index())?;
    }
    if name == "StartScanning" && !app.bridge.enabled.load(Ordering::SeqCst) {
        return Err("Turn IntiHA on before scanning".into());
    }
    Ok(())
}
fn external_message_value(message: &ButtplugClientMessageVariant) -> Result<Value, String> {
    use buttplug_server::message::ButtplugClientMessageVariant::*;
    match message {
        V0(m) => serde_json::to_value(m),
        V1(m) => serde_json::to_value(m),
        V2(m) => serde_json::to_value(m),
        V3(m) => serde_json::to_value(m),
        V4(m) => serde_json::to_value(m),
    }
    .map_err(|_| "Cannot inspect command".to_owned())
}
fn error_message(id: u32, error: &str) -> Message {
    Message::Text(
        json!([{"Error":{"Id":id,"ErrorCode":3,"ErrorMessage":error}}])
            .to_string()
            .into(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn token_separation_and_comparison() {
        assert!(token_matches("secret", "secret"));
        assert!(!token_matches("Secret", "secret"));
        let config = Config {
            bridge_token: "a".repeat(32),
            client_token: "a".repeat(32),
            bind: "127.0.0.1".into(),
            client_port: 1,
            bridge_port: 2,
        };
        assert!(config.validate().is_err());
    }
    #[tokio::test]
    async fn starts_disabled_and_uses_official_v3_handshake() {
        let app = App::new(Config {
            bridge_token: "a".repeat(32),
            client_token: "b".repeat(32),
            bind: "127.0.0.1".into(),
            client_port: 0,
            bridge_port: 0,
        })
        .await
        .unwrap();
        assert!(!app.bridge.enabled.load(Ordering::SeqCst));
        let reply = app
            .administer(json!({"op":"command","message":{"RequestDeviceList":{"Id":12}}}))
            .await
            .unwrap();
        assert_eq!(reply["message"]["DeviceList"]["Id"], 12);
        assert_eq!(reply["message"]["DeviceList"]["Devices"], json!([]));
        let movement = app.administer(json!({"op":"command","message":{"ScalarCmd":{"Id":13,"DeviceIndex":0,"Scalars":[{"Index":0,"Scalar":1.0,"ActuatorType":"Vibrate"}]}}})).await;
        assert!(movement.is_err());
        app.shutdown().await;
    }

    fn test_config() -> Config {
        Config {
            bridge_token: "a".repeat(32),
            client_token: "b".repeat(32),
            bind: "127.0.0.1".into(),
            client_port: 0,
            bridge_port: 0,
        }
    }

    #[tokio::test]
    async fn strict_stop_propagates_hardware_failure_and_invalidates_the_session() {
        let app = App::new(test_config()).await.unwrap();
        let (tx, mut rx) = mpsc::channel(8);
        let epoch = app.bridge.attach(tx);
        let session = Arc::new(crate::bridge::Session::new(
            "AA:BB:CC:DD:EE:FF",
            "one",
            false,
        ));
        app.bridge.register(session.clone());
        let a = app.clone();
        let task = tokio::spawn(async move { a.stop_all().await });
        let request = rx.recv().await.unwrap();
        assert_eq!(request["op"], "disconnect");
        assert!(!session.connected.load(Ordering::SeqCst));
        app.bridge.response(
            epoch,
            &json!({"id":request["id"],"ok":false,"error":"Stop write failed"}),
        );
        assert!(
            task.await
                .unwrap()
                .unwrap_err()
                .contains("Stop write failed")
        );
        assert!(!app.bridge.reconnect_paused.load(Ordering::SeqCst));
        assert!(
            app.bridge
                .last_error
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .contains("could not be confirmed")
        );
        app.bridge.detach(epoch);
        app.shutdown().await;
    }

    #[tokio::test]
    async fn cancelled_stop_cannot_leave_reconnections_paused() {
        let app = App::new(test_config()).await.unwrap();
        let (tx, mut rx) = mpsc::channel(8);
        let epoch = app.bridge.attach(tx);
        app.bridge.register(Arc::new(crate::bridge::Session::new(
            "AA:BB:CC:DD:EE:FF",
            "one",
            false,
        )));
        let a = app.clone();
        let task = tokio::spawn(async move { a.stop_all().await });
        rx.recv().await.unwrap();
        assert!(app.bridge.reconnect_paused.load(Ordering::SeqCst));
        task.abort();
        let _ = task.await;
        assert!(!app.bridge.reconnect_paused.load(Ordering::SeqCst));
        app.bridge.detach(epoch);
        app.shutdown().await;
    }

    #[tokio::test]
    async fn queued_output_is_rejected_after_stop_barrier() {
        let app = App::new(test_config()).await.unwrap();
        app.bridge.control_epoch.store(2, Ordering::SeqCst);
        let result=app.administer(json!({"op":"command","_control_epoch":1,"message":{"ScalarCmd":{"Id":1,"DeviceIndex":0,"Scalars":[{"Index":0,"Scalar":1.0,"ActuatorType":"Vibrate"}]}}})).await;
        assert!(result.unwrap_err().contains("cancelled by stop"));
        app.shutdown().await;
    }
}
