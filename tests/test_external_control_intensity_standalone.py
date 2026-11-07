"""Verify Ogg ownership does not make the commanded-intensity sensor unavailable."""
import ast
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).parents[1] / 'custom_components' / 'intiha'

def load_method(path, cls, name):
    tree = ast.parse((root/path).read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    f = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    f.decorator_list = []
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),str(path),'exec'),namespace)
    return namespace[name]

available = load_method('sensor.py','IntiHASensor','available')
number_available = load_method('number.py','IntiHALevel','available')

dev = SimpleNamespace(connected=True,connect_enabled=True,outputs={0:48.0},reported_intensity=None)
coord = SimpleNamespace(enabled=True,engine_connected=True,client_connected=True,_control_paused=False,devices={'bond':dev})
sensor = SimpleNamespace(coordinator=coord,address='bond',key='intensity')
num = SimpleNamespace(coordinator=coord,address='bond')
assert available(sensor) is True, 'Ogg external client must not disable commanded sensor'
assert number_available(num) is False, 'manual slider remains protected from Ogg ownership'
dev.connected = False
assert available(sensor) is False, 'disconnected device cannot claim live intensity'
coord.client_connected = False
dev.connected = True
assert available(sensor) is True
assert number_available(num) is True
print('Commanded-intensity and manual-control ownership invariants passed')
