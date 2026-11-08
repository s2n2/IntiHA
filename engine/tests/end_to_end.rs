//! Real TCP/WebSocket tests with the official engine and a fake HA GATT peer.
//! No physical radio is simulated as evidence of hardware compatibility.
use futures::{SinkExt, StreamExt};
use intiha_engine::server::{App, Config};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, atomic::Ordering},
    time::{Duration, Instant},
};
use tokio::{
    net::TcpListener,
    sync::{broadcast, mpsc},
    task::JoinHandle,
};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const ADDRESS: &str = "AA:BB:CC:DD:EE:01";
fn official_wevibe_services() -> Vec<Value> {
    use buttplug_server_device_config::{ProtocolCommunicationSpecifier, load_protocol_configs};
    let config = load_protocol_configs(&None, &None, false)
        .unwrap()
        .finish()
        .unwrap();
    let mut services = HashMap::<String, HashMap<String, Value>>::new();
    for (name, specifiers) in config.base_communication_specifiers() {
        if name != "wevibe" && name != "wevibe-8bit" {
            continue;
        }
        for specifier in specifiers {
            if let ProtocolCommunicationSpecifier::BluetoothLE(ble) = specifier {
                for (service, characteristics) in ble.services() {
                    for uuid in characteristics.values() {
                        services.entry(service.to_string()).or_default().insert(
                            uuid.to_string(),
                            json!({"uuid":uuid.to_string(),"handle":1,"properties":["read","write","write-without-response","notify"]}),
                        );
                    }
                }
            }
        }
    }
    services
        .into_iter()
        .map(|(uuid, characteristics)| {
            json!({"uuid":uuid,"characteristics":characteristics.into_values().collect::<Vec<_>>()})
        })
        .collect()
}

struct Harness {
    app: Arc<App>,
    client_url: String,
    commands: mpsc::Sender<Value>,
    events: broadcast::Sender<Value>,
    writes: Arc<Mutex<Vec<Value>>>,
    tasks: Vec<JoinHandle<()>>,
}
impl Harness {
    async fn start() -> Self {
        Self::start_with_stop_failure(false).await
    }
    async fn start_with_stop_failure(fail_stop: bool) -> Self {
        let config = Config {
            bind: "127.0.0.1".into(),
            client_port: 0,
            bridge_port: 0,
        };
        let app = App::new(config.clone()).await.unwrap();
        let client_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let bridge_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let client_url = format!("ws://{}/", client_listener.local_addr().unwrap());
        let bridge_url = format!("ws://{}/bridge", bridge_listener.local_addr().unwrap());
        let a = app.clone();
        let b = app.clone();
        let c = app.clone();
        let tasks = vec![
            tokio::spawn(async move {
                axum::serve(client_listener, a.client_router())
                    .await
                    .unwrap();
            }),
            tokio::spawn(async move {
                axum::serve(bridge_listener, b.bridge_router())
                    .await
                    .unwrap();
            }),
            tokio::spawn(async move {
                c.supervise().await;
            }),
        ];
        let (mut socket, _) = connect_async(bridge_url).await.unwrap();
        socket
            .send(Message::Text(
                json!({"type":"hello","protocol":1,"enabled":true,"auto_reconnect":true})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
        let (commands, mut command_rx) = mpsc::channel::<Value>(32);
        let (events, _) = broadcast::channel::<Value>(128);
        let event_sender = events.clone();
        let writes = Arc::new(Mutex::new(Vec::new()));
        let captured = writes.clone();
        let services = official_wevibe_services();
        let task = tokio::spawn(async move {
            let mut stop_plans = HashMap::<String, Vec<Value>>::new();
            loop {
                tokio::select! {
                    command = command_rx.recv() => {
                        let Some(command) = command else { break; };
                        if socket.send(Message::Text(command.to_string().into())).await.is_err() { break; }
                    }
                    incoming = socket.next() => {
                        let Some(Ok(message)) = incoming else { break; };
                        match message {
                            Message::Text(text) => {
                                let value: Value = serde_json::from_str(&text).unwrap();
                                let _ = event_sender.send(value.clone());
                                if value["type"] != "request" { continue; }
                                if fail_stop && value["op"] == "prepare_stop" {
                                    let response = json!({"type":"response","id":value["id"],"ok":false,"error":"Simulated stop write failed"});
                                    if socket.send(Message::Text(response.to_string().into())).await.is_err() { break; }
                                    continue;
                                }
                                let result = match value["op"].as_str().unwrap() {
                                    "connect" => json!({"services":services}),
                                    "read" => json!({"data":[100]}),
                                    "write" => { captured.lock().unwrap().push(value.clone()); json!({}) }
                                    "prepare_stop" => {
                                        let plan = value["writes"].as_array().unwrap().clone();
                                        assert!(!plan.is_empty());
                                        captured.lock().unwrap().extend(plan.clone());
                                        stop_plans.insert(value["connection_id"].as_str().unwrap().to_owned(), plan.clone());
                                        json!({"verified":true,"connection_id":value["connection_id"],"write_count":plan.len()})
                                    }
                                    "set_stop_plan" => {
                                        let writes=value["writes"].as_array().unwrap().clone(); assert!(!writes.is_empty());
                                        stop_plans.insert(value["connection_id"].as_str().unwrap().to_owned(),writes); json!({})
                                    }
                                    "disconnect" => {
                                        if let Some(plan)=stop_plans.remove(value["connection_id"].as_str().unwrap()) { captured.lock().unwrap().extend(plan); }
                                        json!({})
                                    }
                                    "stop" | "keepalive" => {
                                        // Cached stop / keep-alive: replay the verified plan in place.
                                        let id = value["connection_id"].as_str().unwrap();
                                        if let Some(plan) = stop_plans.get(id) { captured.lock().unwrap().extend(plan.clone()); }
                                        json!({"verified":true,"connection_id":id})
                                    }
                                    "scan"|"subscribe"|"unsubscribe" => json!({}),
                                    other => panic!("Unexpected operation {other}"),
                                };
                                if socket.send(Message::Text(json!({"type":"response","id":value["id"],"ok":true,"result":result}).to_string().into())).await.is_err() { break; }
                            }
                            Message::Ping(data) => { let _ = socket.send(Message::Pong(data)).await; }
                            Message::Close(_) => break,
                            _ => {}
                        }
                    }
                }
            }
        });
        let mut h = Self {
            app,
            client_url,
            commands,
            events,
            writes,
            tasks,
        };
        h.tasks.push(task);
        h
    }
    async fn send(&self, value: Value) {
        self.commands.send(value).await.unwrap();
    }
    async fn wait(
        rx: &mut broadcast::Receiver<Value>,
        predicate: impl Fn(&Value) -> bool,
    ) -> Value {
        tokio::time::timeout(Duration::from_secs(12), async {
            loop {
                let value = rx.recv().await.unwrap();
                if predicate(&value) {
                    return value;
                }
            }
        })
        .await
        .expect("Expected bridge event before timeout")
    }
    async fn ready(&self) -> Value {
        self.ready_named("Rave").await
    }
    async fn ready_named(&self, name: &str) -> Value {
        let mut events = self.events.subscribe();
        self.send(json!({"type":"advertisement","address":ADDRESS,"name":name,"service_uuids":[],"manufacturer_data":{},"rssi":-45,"source":"mock-proxy"})).await;
        Self::wait(&mut events, |v| {
            v["type"] == "devices" && v["devices"][0]["ready"] == true
        })
        .await["devices"][0]
            .clone()
    }
    async fn admin(&self, mut value: Value) -> Value {
        let mut events = self.events.subscribe();
        value["type"] = "admin".into();
        value["id"] = 99.into();
        self.send(value).await;
        Self::wait(&mut events, |v| {
            v["type"] == "admin_result" && v["id"] == 99
        })
        .await
    }
    async fn finish(self) {
        self.app.shutdown().await;
        for task in self.tasks {
            task.abort();
        }
    }
}

#[tokio::test]
async fn real_engine_uses_gatt_stop_plan_and_never_pulses_on_connect() {
    let h = Harness::start().await;
    let device = h.ready().await;
    assert_eq!(device["DeviceName"], "WeVibe Rave");
    assert_eq!(device["address"], ADDRESS);
    let writes = h.writes.lock().unwrap().clone();
    assert!(
        writes.len() >= 2,
        "Initialise stopped and verify the cached stop"
    );
    assert!(
        writes
            .iter()
            .all(|v| v["data"] == json!([15, 0, 0, 0, 0, 0, 0, 0])),
        "No motor pulse on connection"
    );
    let command=h.admin(json!({"op":"command","connection_id":device["connection_id"],"message":{"ScalarCmd":{"Id":2,"DeviceIndex":device["DeviceIndex"],"Scalars":[{"Index":0,"Scalar":0.5,"ActuatorType":"Vibrate"}]}}})).await;
    assert_eq!(command["ok"], true, "{command}");
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if h.writes
                .lock()
                .unwrap()
                .iter()
                .any(|v| v["data"] == json!([15, 3, 0, 136, 0, 3, 0, 0]))
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("Official scalar command must reach GATT");
    // Exercise the manual dead-man independently of long discovery operations.
    *h.app.bridge.manual_deadline.lock().unwrap() = Some(Instant::now() - Duration::from_secs(1));
    let mut events = h.events.subscribe();
    Harness::wait(&mut events, |v| v["type"] == "output_clear").await;
    assert_eq!(
        h.writes.lock().unwrap().last().unwrap()["data"],
        json!([15, 0, 0, 0, 0, 0, 0, 0])
    );
    h.finish().await;
}

#[tokio::test]
async fn official_v3_and_v4_clients_connect_without_tokens_and_have_exclusive_control() {
    let h = Harness::start().await;
    let device = h.ready().await;
    for version in [3, 4] {
        let (mut socket, _) = connect_async(&h.client_url).await.unwrap();
        let handshake = if version == 3 {
            json!({"RequestServerInfo":{"Id":1,"ClientName":"Integration test","MessageVersion":3}})
        } else {
            json!({"RequestServerInfo":{"Id":1,"ClientName":"Integration test","ProtocolVersionMajor":4,"ProtocolVersionMinor":0}})
        };
        socket
            .send(Message::Text(json!([handshake]).to_string().into()))
            .await
            .unwrap();
        let response = tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                match socket.next().await.unwrap().unwrap() {
                    Message::Text(t) => break serde_json::from_str::<Value>(&t).unwrap(),
                    Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
                    _ => {}
                }
            }
        })
        .await
        .unwrap();
        assert!(response[0].get("ServerInfo").is_some(), "{response}");
        assert!(
            connect_async(&h.client_url).await.is_err(),
            "A second controller must be refused"
        );
        let blocked=h.admin(json!({"op":"command","message":{"ScalarCmd":{"Id":2,"DeviceIndex":device["DeviceIndex"],"Scalars":[{"Index":0,"Scalar":1.0,"ActuatorType":"Vibrate"}]}}})).await;
        assert_eq!(blocked["ok"], false);
        let mut events = h.events.subscribe();
        socket.close(None).await.unwrap();
        Harness::wait(&mut events, |v| {
            v["type"] == "status" && v["client_connected"] == false
        })
        .await;
    }
    h.finish().await;
}

#[tokio::test]
async fn external_v3_client_can_drive_existing_proxy_device_and_release_it_without_reconnect() {
    let h = Harness::start().await;
    let device = h.ready().await;
    let connection_id = device["connection_id"].as_str().unwrap().to_owned();
    let (mut socket, _) = connect_async(&h.client_url).await.unwrap();
    let writes_before = h.writes.lock().unwrap().len();
    socket
        .send(Message::Text(
            json!([{"RequestServerInfo":{"Id":1,"ClientName":"External command test","MessageVersion":3}}])
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(t) if serde_json::from_str::<Value>(&t).unwrap()[0].get("ServerInfo").is_some() => break,
            Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
            _ => {}
        }
    }
    // Normal Buttplug clients may issue StopAll as a safety command before
    // movement. It must stop outputs without retiring the proxy GATT session.
    socket
        .send(Message::Text(
            json!([{"StopAllDevices":{"Id":2}}]).to_string().into(),
        ))
        .await
        .unwrap();
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(t) => {
                let value = serde_json::from_str::<Value>(&t).unwrap();
                if value[0].get("Ok").is_some() {
                    break;
                }
                if value[0].get("Error").is_some() {
                    panic!("StopAllDevices failed: {value}");
                }
            }
            Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
            _ => {}
        }
    }
    let session_after_stop = h.app.bridge.session(&connection_id).unwrap();
    assert!(session_after_stop.connected.load(Ordering::SeqCst));
    assert!(session_after_stop.ready.load(Ordering::SeqCst));

    socket
        .send(Message::Text(
            json!([{"ScalarCmd":{"Id":3,"DeviceIndex":device["DeviceIndex"],"Scalars":[{"Index":0,"Scalar":0.4,"ActuatorType":"Vibrate"}]}}])
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    let response = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match socket.next().await.unwrap().unwrap() {
                Message::Text(t) => {
                    let value = serde_json::from_str::<Value>(&t).unwrap();
                    if value[0].get("Ok").is_some() || value[0].get("Error").is_some() {
                        break value;
                    }
                }
                Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
                _ => {}
            }
        }
    })
    .await
    .unwrap();
    assert!(response[0].get("Ok").is_some(), "{response}");
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let reached_gatt = {
                let writes = h.writes.lock().unwrap();
                writes.len() > writes_before
                    && writes[writes_before..]
                        .iter()
                        .any(|v| v["data"] != json!([15, 0, 0, 0, 0, 0, 0, 0]))
            };
            if reached_gatt {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("External command must reach the HA-proxied GATT session");
    let mut events = h.events.subscribe();
    socket.close(None).await.unwrap();
    Harness::wait(&mut events, |v| {
        v["type"] == "status" && v["client_connected"] == false
    })
    .await;
    let session = h.app.bridge.session(&connection_id).unwrap();
    assert!(session.connected.load(Ordering::SeqCst));
    assert!(session.ready.load(Ordering::SeqCst));
    h.finish().await;
}


/// Regression: IntiHA used to stop devices by writing a cached packet behind
/// the official driver's back. The driver's last-command cache still held the
/// old level, so the next identical ScalarCmd (every repeated pulse, and every
/// pattern that dips to zero) was silently skipped and the device stayed off.
#[tokio::test]
async fn repeated_pulse_after_external_stop_reaches_the_device_again() {
    let h = Harness::start().await;
    let device = h.ready().await;
    let index = device["DeviceIndex"].clone();
    let (mut socket, _) = connect_async(&h.client_url).await.unwrap();
    macro_rules! request {
        ($message:expr) => {{
            socket.send(Message::Text(json!([$message]).to_string().into())).await.unwrap();
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    match socket.next().await.unwrap().unwrap() {
                        Message::Text(t) => {
                            let value = serde_json::from_str::<Value>(&t).unwrap();
                            if value[0].get("Ok").is_some() || value[0].get("ServerInfo").is_some() {
                                break value;
                            }
                            if value[0].get("Error").is_some() { panic!("request failed: {value}"); }
                        }
                        Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
                        _ => {}
                    }
                }
            }).await.expect("response")
        }};
    }
    let on = |id: u32| json!({"ScalarCmd":{"Id":id,"DeviceIndex":index,"Scalars":[{"Index":0,"Scalar":0.5,"ActuatorType":"Vibrate"}]}});
    let stop = json!([15, 0, 0, 0, 0, 0, 0, 0]);
    let wait_for_output = |after: usize| {
        let writes = h.writes.clone();
        let stop = stop.clone();
        async move {
            tokio::time::timeout(Duration::from_secs(3), async {
                loop {
                    if writes.lock().unwrap()[after..].iter().any(|w| w["data"] != stop) { break; }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            }).await.is_ok()
        }
    };
    request!(json!({"RequestServerInfo":{"Id":1,"ClientName":"Pulse repeat test","MessageVersion":3}}));
    for round in 0..3u32 {
        let before = h.writes.lock().unwrap().len();
        request!(on(10 + round * 2));
        assert!(wait_for_output(before).await, "pulse {round} never reached GATT");
        request!(json!({"StopDeviceCmd":{"Id":11 + round * 2,"DeviceIndex":index}}));
        tokio::time::sleep(Duration::from_millis(200)).await;
        assert_eq!(h.writes.lock().unwrap().last().unwrap()["data"], stop, "pulse {round} was not stopped");
    }
    socket.close(None).await.unwrap();
    h.finish().await;
}

/// Idle devices get silent keep-alive stops; a running device never does.
#[tokio::test]
async fn keepalive_resends_stop_only_while_idle() {
    let h = Harness::start().await;
    h.app.bridge.keepalive_idle_ms.store(700, Ordering::SeqCst);
    let device = h.ready().await;
    let index = device["DeviceIndex"].clone();
    let mut events = h.events.subscribe();
    // Idle after connecting: a keep-alive request must arrive.
    Harness::wait(&mut events, |v| v["type"] == "request" && v["op"] == "keepalive").await;

    let (mut socket, _) = connect_async(&h.client_url).await.unwrap();
    macro_rules! request {
        ($message:expr) => {{
            socket.send(Message::Text(json!([$message]).to_string().into())).await.unwrap();
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    match socket.next().await.unwrap().unwrap() {
                        Message::Text(t) => {
                            let value = serde_json::from_str::<Value>(&t).unwrap();
                            if value[0].get("Ok").is_some() || value[0].get("ServerInfo").is_some() { break value; }
                            if value[0].get("Error").is_some() { panic!("request failed: {value}"); }
                        }
                        Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
                        _ => {}
                    }
                }
            }).await.expect("response")
        }};
    }
    request!(json!({"RequestServerInfo":{"Id":1,"ClientName":"Keepalive test","MessageVersion":3}}));
    request!(json!({"ScalarCmd":{"Id":2,"DeviceIndex":index,"Scalars":[{"Index":0,"Scalar":0.5,"ActuatorType":"Vibrate"}]}}));
    // While running, no keep-alive stop may be sent, even after the idle time.
    let mut events = h.events.subscribe();
    let stopped_while_running = tokio::time::timeout(Duration::from_millis(2500), async {
        loop {
            let v = events.recv().await.unwrap();
            if v["type"] == "request" && v["op"] == "keepalive" { break; }
        }
    }).await;
    assert!(stopped_while_running.is_err(), "keep-alive stop must never interrupt output");
    let last = h.writes.lock().unwrap().last().unwrap()["data"].clone();
    assert_ne!(last, json!([15, 0, 0, 0, 0, 0, 0, 0]), "device must still be running");

    // After an explicit stop it is idle again and keep-alives resume.
    request!(json!({"StopDeviceCmd":{"Id":3,"DeviceIndex":index}}));
    let mut events = h.events.subscribe();
    Harness::wait(&mut events, |v| v["type"] == "request" && v["op"] == "keepalive").await;
    assert_eq!(h.writes.lock().unwrap().last().unwrap()["data"], json!([15, 0, 0, 0, 0, 0, 0, 0]));
    socket.close(None).await.unwrap();
    h.finish().await;
}

#[tokio::test]
async fn external_stop_is_a_barrier_and_cannot_bounce_back_to_previous_output() {
    let h = Harness::start().await;
    let device = h.ready().await;
    let connection_id = device["connection_id"].as_str().unwrap().to_owned();
    let (mut socket, _) = connect_async(&h.client_url).await.unwrap();
    socket
        .send(Message::Text(
            json!([{"RequestServerInfo":{"Id":1,"ClientName":"Stop barrier test","MessageVersion":3}}])
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(t) if serde_json::from_str::<Value>(&t).unwrap()[0].get("ServerInfo").is_some() => break,
            Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
            _ => {}
        }
    }

    socket
        .send(Message::Text(
            json!([{"ScalarCmd":{"Id":2,"DeviceIndex":device["DeviceIndex"],"Scalars":[{"Index":0,"Scalar":1.0,"ActuatorType":"Vibrate"}]}}])
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(t) => {
                let value = serde_json::from_str::<Value>(&t).unwrap();
                if value[0].get("Ok").is_some() { break; }
                if value[0].get("Error").is_some() { panic!("100% output failed: {value}"); }
            }
            Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
            _ => {}
        }
    }
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if h.writes.lock().unwrap().iter().any(|write| {
                write["data"].as_array().is_some_and(|data| {
                    data != &vec![json!(15),json!(0),json!(0),json!(0),json!(0),json!(0),json!(0),json!(0)]
                })
            }) { break; }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.expect("100% command must reach GATT before the stop test");

    socket
        .send(Message::Text(json!([{"StopAllDevices":{"Id":3}}]).to_string().into()))
        .await
        .unwrap();
    loop {
        match socket.next().await.unwrap().unwrap() {
            Message::Text(t) => {
                let value = serde_json::from_str::<Value>(&t).unwrap();
                if value[0].get("Ok").is_some() { break; }
                if value[0].get("Error").is_some() { panic!("StopAllDevices failed: {value}"); }
            }
            Message::Ping(v) => socket.send(Message::Pong(v)).await.unwrap(),
            _ => {}
        }
    }

    // The response is not sent until the verified physical stop has been put
    // last on the BLE path. Give any protocol keepalive several update periods
    // to prove it cannot restore the old 100% state afterwards.
    tokio::time::sleep(Duration::from_millis(600)).await;
    let writes = h.writes.lock().unwrap().clone();
    assert_eq!(
        writes.last().unwrap()["data"],
        json!([15, 0, 0, 0, 0, 0, 0, 0]),
        "no queued/keepalive output may run after StopAllDevices returns"
    );
    let session = h.app.bridge.session(&connection_id).unwrap();
    assert!(session.connected.load(Ordering::SeqCst));
    assert!(session.ready.load(Ordering::SeqCst));
    socket.close(None).await.unwrap();
    h.finish().await;
}

#[tokio::test]
async fn reconnect_uses_new_session_and_keeps_output_stopped() {
    let h = Harness::start().await;
    let first = h.ready().await;
    let old = first["connection_id"].clone();
    h.send(
        json!({"type":"disconnected","address":ADDRESS,"connection_id":old,"reason":"proxy lost"}),
    )
    .await;
    let second = h.ready().await;
    assert_ne!(second["connection_id"], old);
    assert_eq!(
        second["DeviceIndex"], first["DeviceIndex"],
        "The regression exercises a reused public index"
    );
    let stale = h.admin(json!({"op":"command","connection_id":old,"message":{"ScalarCmd":{"Id":3,"DeviceIndex":second["DeviceIndex"],"Scalars":[{"Index":0,"Scalar":0.75,"ActuatorType":"Vibrate"}]}}})).await;
    assert_eq!(stale["ok"], false);
    assert!(
        stale["error"]
            .as_str()
            .unwrap()
            .contains("connection changed")
    );
    h.send(json!({"type":"disconnected","address":ADDRESS,"connection_id":old,"reason":"late stale callback"})).await;
    let state = h.admin(json!({"op":"status"})).await;
    assert_eq!(state["ok"], true);
    assert!(
        h.app
            .bridge
            .session(second["connection_id"].as_str().unwrap())
            .unwrap()
            .connected
            .load(Ordering::SeqCst)
    );
    assert!(
        h.writes
            .lock()
            .unwrap()
            .iter()
            .all(|v| v["data"] == json!([15, 0, 0, 0, 0, 0, 0, 0]))
    );
    h.finish().await;
}

#[tokio::test]
async fn compatible_devices_connect_automatically_without_selection_or_search() {
    let h = Harness::start().await;
    let first = h.ready().await;
    let mut events = h.events.subscribe();
    let other = "AA:BB:CC:DD:EE:02";
    h.send(json!({"type":"advertisement","address":other,"name":"Rave","service_uuids":[],"manufacturer_data":{}})).await;
    Harness::wait(&mut events, |v| {
        v["type"] == "discovered" && v["address"] == other
    })
    .await;
    let inventory = Harness::wait(&mut events, |v| {
        v["type"] == "devices"
            && v["devices"].as_array().is_some_and(|devices| {
                devices
                    .iter()
                    .any(|d| d["address"] == other && d["ready"] == true)
            })
    })
    .await;
    assert_eq!(inventory["devices"].as_array().unwrap().len(), 2);
    let second = inventory["devices"]
        .as_array()
        .unwrap()
        .iter()
        .find(|device| device["address"] == other)
        .unwrap();
    assert_ne!(first["connection_id"], second["connection_id"]);
    assert!(h.app.bridge.session_for(other).is_some());
    assert!(
        h.writes
            .lock()
            .unwrap()
            .iter()
            .all(|write| write["data"] == json!([15, 0, 0, 0, 0, 0, 0, 0]))
    );
    h.finish().await;
}

#[tokio::test]
async fn official_bond_profile_generates_and_verifies_its_own_stop() {
    let h = Harness::start().await;
    let mut events = h.events.subscribe();
    let device = h.ready_named("Bond").await;
    assert!(device["DeviceName"].as_str().unwrap().contains("Bond"));
    assert_eq!(device["ready"], true);
    let mut prepared = false;
    while let Ok(event) = events.try_recv() {
        if event["op"] == "prepare_stop" {
            assert!(!event["writes"].as_array().unwrap().is_empty());
            prepared = true;
        }
    }
    assert!(
        prepared,
        "Bond must pass the real transport verification stage"
    );
    assert!(!h.writes.lock().unwrap().is_empty());
    h.finish().await;
}

#[tokio::test]
async fn failed_atomic_stop_never_enables_bond_controls() {
    let h = Harness::start_with_stop_failure(true).await;
    let mut events = h.events.subscribe();
    h.send(json!({"type":"advertisement","address":ADDRESS,"name":"Bond","service_uuids":[],"manufacturer_data":{}})).await;
    let failure = Harness::wait(&mut events, |v| {
        v["type"] == "device_status"
            && v["phase"] == "retrying"
            && v["error"]
                .as_str()
                .is_some_and(|e| e.contains("Simulated stop write failed"))
    })
    .await;
    assert!(failure["error"].as_str().unwrap().contains("control refused"));
    assert!(
        h.app
            .bridge
            .all_sessions()
            .iter()
            .all(|s| !s.ready.load(Ordering::SeqCst))
    );
    h.finish().await;
}
