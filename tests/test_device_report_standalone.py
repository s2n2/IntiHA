"""Run `python tests/test_device_report_standalone.py` without Home Assistant."""
import ast
from pathlib import Path
from typing import Any

source = Path(__file__).parents[1] / 'custom_components' / 'intiha' / 'ble.py'
module = ast.parse(source.read_text())
constants = next(node for node in module.body if isinstance(node, ast.Assign) and
                 any(isinstance(t, ast.Name) and t.id == 'WEVIBE_PATTERNS' for t in node.targets))
decoder = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'decode_wevibe_info')
ns = {'Any': Any}
exec(compile(ast.Module(body=[constants, decoder], type_ignores=[]), str(source), 'exec'), ns)
decode = ns['decode_wevibe_info']
assert decode(bytes([1, 3, 0, 0, 0, 65, 0xA4, 2])) == {
    'intensity': 67, 'internal_intensity': 67, 'external_intensity': 27,
    'pattern': 'vibrate', 'pattern_code': 3,
}
assert decode(bytes([1, 0, 0, 0, 0, 65, 0xFF, 2]))['intensity'] == 0
assert decode(bytes([2, 3, 0, 0, 0, 65, 0xA4, 2])) is None
assert decode(bytes([1, 3, 0])) is None
print('Device report decoder standalone tests passed.')
