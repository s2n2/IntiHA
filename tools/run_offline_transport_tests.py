"""Run real IntiHA transport modules with fake HA imports and fake BLE.

Requires pytest, pytest-asyncio and aiohttp. This is a limited transport test,
not a substitute for the normal Home Assistant, Rust or Docker CI jobs.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

STUBS = '"""Offline harness: real IntiHA transport code; mocked HA/Bleak import surface."""\nimport asyncio\nimport sys\nimport types\nfrom enum import Enum\nfrom pathlib import Path\nfrom types import SimpleNamespace\nimport pytest\n\nROOT=Path(__REPOSITORY_ROOT__)\nsys.path.insert(0,str(ROOT))\npackage=types.ModuleType(\'custom_components.intiha\')\npackage.__path__=[str(ROOT/\'custom_components/intiha\')]\nsys.modules[\'custom_components.intiha\']=package\nimport custom_components\ncustom_components.intiha=package\n\ndef module(name):\n    m=types.ModuleType(name); sys.modules[name]=m; return m\n\nmodule(\'homeassistant\').__path__=[]\nmodule(\'homeassistant.components\').__path__=[]\nbt=module(\'homeassistant.components.bluetooth\')\nclass BluetoothServiceInfoBleak(SimpleNamespace):\n    pass\nbt.BluetoothServiceInfoBleak=BluetoothServiceInfoBleak\nbt.BluetoothScanningMode=SimpleNamespace(ACTIVE=\'active\')\nbt.BluetoothChange=SimpleNamespace\nfor name in (\'async_register_callback\',\'async_ble_device_from_address\',\'async_discovered_service_info\'):\n    setattr(bt,name,lambda *args,**kw: None)\nconst=module(\'homeassistant.const\')\nconst.Platform=SimpleNamespace(BINARY_SENSOR=\'binary_sensor\',BUTTON=\'button\',NUMBER=\'number\',SENSOR=\'sensor\',SWITCH=\'switch\')\nmodule(\'bleak\').__path__=[]\nmodule(\'bleak.backends\').__path__=[]\nbledev=module(\'bleak.backends.device\')\nclass BLEDevice:\n    def __init__(self,address,name,details):\n        self.address=address; self.name=name; self.details=details\nbledev.BLEDevice=BLEDevice\nretry=module(\'bleak_retry_connector\')\nretry.BleakClientWithServiceCache=type(\'BleakClientWithServiceCache\',(),{})\nasync def missing(*args,**kwargs):\n    raise AssertionError(\'No real Bluetooth backend in offline test harness\')\nretry.establish_connection=missing\n\n@pytest.fixture\ndef hass():\n    class Hass:\n        def async_create_background_task(self,coro,name):\n            return asyncio.create_task(coro,name=name)\n    return Hass()\n'


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="intiha-transport-tests-") as directory:
        target = Path(directory)
        (target / "conftest.py").write_text(
            STUBS.replace("__REPOSITORY_ROOT__", repr(str(root))), encoding="utf-8"
        )
        for name in ("test_ble.py", "test_bridge.py"):
            (target / name).write_bytes((root / "tests" / name).read_bytes())
        (target / "pytest.ini").write_text(
            "[pytest]\nasyncio_mode=auto\nasyncio_default_fixture_loop_scope=function\n",
            encoding="utf-8",
        )
        return subprocess.call([
            sys.executable, "-m", "pytest", str(target),
            "--confcutdir=" + str(target), "-c", str(target / "pytest.ini"), "-q",
        ], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
