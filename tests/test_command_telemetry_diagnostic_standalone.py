"""Standalone tests; no Home Assistant installation required."""
import ast
from pathlib import Path
from types import SimpleNamespace

src = Path(__file__).parents[1] / "custom_components" / "intiha" / "sensor.py"
module = ast.parse(src.read_text())
fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "command_telemetry_state")
ns = {}
exec(compile(ast.Module(body=[fn], type_ignores=[]), str(src), "exec"), ns)
check = ns["command_telemetry_state"]
coord = SimpleNamespace(enabled=True, engine_connected=True, client_connected=True)
dev = SimpleNamespace(connected=False, connect_enabled=True, outputs={})
assert check(coord, dev) == "bluetooth_disconnected"
dev.connected = True
assert check(coord, dev) == "awaiting_external_commands"
dev.outputs = {0: 48.0}
assert check(coord, dev) == "receiving_external_commands"
coord.client_connected = False
assert check(coord, dev) == "manual_control_ready"
coord.engine_connected = False
assert check(coord, dev) == "engine_disconnected"
coord.engine_connected = True
dev.connect_enabled = False
assert check(coord, dev) == "device_disabled"
coord.enabled = False
assert check(coord, dev) == "server_disabled"
print("Command telemetry diagnostic states passed")
