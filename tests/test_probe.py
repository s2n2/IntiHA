import asyncio
from types import SimpleNamespace
import pytest
from intiha_core.probe import Path, ProbeError, ProxyProbe, select_path, validate_address

ADDRESS = 'D0:2E:AB:3A:D5:B4'
SOURCE = 'AA:BB:CC:DD:EE:01'


class Backend:
    instances = []
    failure = None
    pair_supported = True
    slow = False
    cleanup_failure = False
    wrong_source = False
    def __init__(self, device, **kwargs):
        self._source = 'OTHER' if self.wrong_source else device.details['source']
        self._device_info = SimpleNamespace(esphome_version='test', model='ESP32 D1 Mini')
        self.callback = kwargs['disconnected_callback']
        self.connected = False
        self.cleaned = False
        self.pair_requested = False
        self.services = [SimpleNamespace(uuid='service-uuid', handle=1,
                           characteristics=[SimpleNamespace(uuid='characteristic-uuid', handle=27,
                                                            properties=['write', 'read'])])]
        self.instances.append(self)
    @property
    def is_connected(self): return self.connected
    async def connect(self, pair, **kwargs):
        self.pair_requested = pair
        if self.slow: await asyncio.sleep(5)
        if pair and not self.pair_supported: raise NotImplementedError('Pairing unsupported')
        if self.failure: raise self.failure
        self.connected = True
    async def disconnect(self):
        self.cleaned = True
        self.connected = False
        self.callback()
        if self.cleanup_failure: raise RuntimeError('disconnect timed out')
    async def write_gatt_char(self, *args, **kwargs):
        raise AssertionError('A read-only probe must NEVER write')


@pytest.fixture(autouse=True)
def reset_backend():
    Backend.instances = []
    Backend.failure = None
    Backend.pair_supported = True
    Backend.slow = False
    Backend.cleanup_failure = False
    Backend.wrong_source = False


def paths():
    dev = SimpleNamespace(details={'source': SOURCE})
    return [Path(SOURCE, 'Garage Bluetooth Proxy', -40, dev, Backend, lambda: True)]


async def test_probe_pins_garage_and_never_writes():
    probe = ProxyProbe()
    result = await probe.run(paths(), ADDRESS, 'garage')
    assert result['status'] == 'passed_connection_test_only'
    assert result['attempts'][0]['actual_source'] == SOURCE
    assert result['attempts'][0]['pairing_completed']
    assert not result['protected_motor_write_verified']
    assert not result['attempts'][0]['unexpected_disconnect']
    assert Backend.instances[0].cleaned


async def test_probe_reconnect_cycles_use_fresh_clients():
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE, cycles=3, cooldown=.001)
    assert len(Backend.instances) == 3
    assert all(b.cleaned for b in Backend.instances)
    assert result['status'] == 'passed_connection_test_only'


async def test_probe_stops_at_first_failure():
    Backend.failure = RuntimeError('Insufficient encryption')
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE, cycles=20)
    assert result['status'] == 'failed'
    assert len(result['attempts']) == 1
    assert 'encryption' in result['attempts'][0]['error']
    assert Backend.instances[0].cleaned


async def test_probe_unsupported_pairing_not_reported_as_success():
    Backend.pair_supported = False
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE)
    assert result['status'] == 'failed'
    assert not result['attempts'][0]['pairing_completed']


async def test_probe_no_pair_option():
    Backend.pair_supported = False
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE, pair=False)
    assert result['status'] == 'passed_connection_test_only'
    assert not result['attempts'][0]['pairing_completed']


async def test_probe_refuses_unverified_source():
    Backend.wrong_source = True
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE)
    assert result['status'] == 'failed'
    assert not Backend.instances[0].pair_requested
    assert Backend.instances[0].cleaned


async def test_probe_cancellation_cleans_connection():
    Backend.slow = True
    probe = ProxyProbe()
    task = asyncio.create_task(probe.run(paths(), ADDRESS, SOURCE))
    await asyncio.sleep(.02)
    await probe.cancel()
    assert task.cancelled()
    assert Backend.instances[0].cleaned
    assert not probe.running and probe.last_result['status'] == 'cancelled'


async def test_probe_timeout_cleans_connection():
    Backend.slow = True
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE, timeout=.01)
    assert result['status'] == 'failed'
    assert Backend.instances[0].cleaned


async def test_probe_cleanup_failure_counts_as_failure():
    Backend.cleanup_failure = True
    result = await ProxyProbe().run(paths(), ADDRESS, SOURCE)
    assert result['status'] == 'failed'
    assert 'disconnect_failed' in result['attempts'][0]['cleanup']


async def test_probe_no_overlap():
    Backend.slow = True
    probe = ProxyProbe()
    task = asyncio.create_task(probe.run(paths(), ADDRESS, SOURCE))
    await asyncio.sleep(.01)
    with pytest.raises(ProbeError, match='already running'):
        await probe.run(paths(), ADDRESS, SOURCE)
    await probe.cancel()


@pytest.mark.parametrize('source', ['', 'bedroom', 'missing-mac'])
def test_no_silent_fallback(source):
    with pytest.raises((ProbeError, ValueError)):
        select_path(paths(), source)


def test_ambiguous_name_refused():
    multiple = paths() + [Path('AA:00', 'Other Garage', -20, None, Backend, lambda: True)]
    with pytest.raises(ProbeError, match='ambiguous'):
        select_path(multiple, 'garage')


def test_busy_proxy_refused():
    path = paths()[0]; path.can_connect = lambda: False
    with pytest.raises(ProbeError, match='no free'):
        select_path([path], 'garage')


@pytest.mark.parametrize('address', ['', 'Bond', 'AA:BB:CC:DD:EE', 'xx:xx:xx:xx:xx:xx', None])
def test_bad_address(address):
    with pytest.raises(ValueError): validate_address(address)
