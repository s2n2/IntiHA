"""No HA dependencies. Protocol readback must be real, safe and session-specific."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
BLE = ROOT / "custom_components/intiha/ble.py"


def extract_bridge(*names):
    module = ast.parse(BLE.read_text())
    c = next(item for item in module.body if isinstance(item, ast.ClassDef) and item.name == "BleBridge")
    methods = [item for item in c.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in names]
    for item in methods:
        item.decorator_list = []
    ns = {
        "asyncio": asyncio, "Any": object, "_Connection": object,
        "WEVIBE_INFO": "f000b000-0451-4000-b000-000000000000",
        "WEVIBE_CONTROL": "f000c000-0451-4000-b000-000000000000",
        "REPORT_INTERVAL": 0.001, "REPORT_READ_TIMEOUT": 0.06,
        "decode_wevibe_info": lambda v: {
            "intensity": 47, "internal_intensity": 47,
            "external_intensity": 13, "pattern": "vibrate", "pattern_code": 3,
        } if v == b"valid" else None,
        "_LOGGER": SimpleNamespace(debug=lambda *a: None),
    }
    class_ast = ast.ClassDef(name="BleBridge", bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[class_ast], type_ignores=[])), str(BLE), "exec"), ns)
    return ns["BleBridge"]


async def main():
    cls = extract_bridge("_start_device_report", "_poll_device_report")
    events = []
    bridge = cls()
    bridge._running = True
    bridge._resetting = False
    bridge._connections = {}
    bridge._locks = {}
    bridge._emit_safely = lambda item: emit(events, item)
    bridge._spawn = lambda coro, name: asyncio.create_task(coro, name=name)
    info = SimpleNamespace(properties=["read"])
    tx = SimpleNamespace(properties=["write"])
    class Services:
        def __init__(self, available):
            self.available = available
        def get_characteristic(self, uuid):
            return self.available.get(uuid)
    addr = "AA:BB:CC:DD:EE:FF"
    con = SimpleNamespace(address=addr, connection_id="session1", report_task=None,
                          client=SimpleNamespace(is_connected=True))
    bridge._connections[addr] = con
    control_id = "f000c000-0451-4000-b000-000000000000"
    info_id = "f000b000-0451-4000-b000-000000000000"
    con.client.services = Services({control_id: tx})
    bridge._start_device_report(con)
    await asyncio.sleep(.01)
    assert events[-1]["status"] == "unsupported"
    assert events[-1]["profile"] == "no_verified_readback"
    assert not [item for item in events if item["type"] == "device_report"]
    events.clear()
    con.client.services = Services({info_id: info, control_id: tx})
    con.client.read_gatt_char = lambda _: asyncio.sleep(0, result=b"valid")
    bridge._start_device_report(con)
    for _ in range(150):
        if any(event["type"] == "device_report" for event in events):
            break
        await asyncio.sleep(.002)
    assert any(event["type"] == "device_report" and event["intensity"] == 47 for event in events)
    assert any(event["type"] == "device_report_status" and event["profile"] == "classic_wevibe_read" for event in events)
    assert not any(event["type"] == "device_report" and "physical_intensity" in event for event in events)
    con.client.is_connected = False
    con.report_task.cancel()
    await asyncio.gather(con.report_task, return_exceptions=True)
    print("PASS: no guessed feedback; classic BLE readback, session scope and stop-safe optional path")


async def emit(events, item):
    events.append(item)


asyncio.run(main())
