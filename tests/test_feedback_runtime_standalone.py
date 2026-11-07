"""Run `python tests/test_feedback_runtime_standalone.py` without HA or BLE."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

BLE_SOURCE = Path(__file__).parents[1]/'custom_components'/'intiha'/'ble.py'
COORD_SOURCE = Path(__file__).parents[1]/'custom_components'/'intiha'/'coordinator.py'


def extract_methods(source, classname, names, scope):
    module = ast.parse(source.read_text())
    class_node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == classname)
    methods = [n for n in class_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(methods) == len(names), [n.name for n in methods]
    for method in methods:
        method.decorator_list = []
    generated = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name=classname, bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    exec(compile(generated, str(source), 'exec'), scope)
    return scope[classname]


async def run():
    trace = []
    sleeper = asyncio.sleep
    class FastAsyncio:
        CancelledError = asyncio.CancelledError
        Lock = asyncio.Lock
        timeout = staticmethod(asyncio.timeout)
        @staticmethod
        async def sleep(sec):
            await sleeper(min(sec / 200, 0.006))

    namespace = {
        'Any': object, '_Connection': object, 'asyncio': FastAsyncio,
        'WEVIBE_INFO':'f000b000-0451-4000-b000-000000000000',
        'REPORT_INTERVAL':1.0, 'REPORT_READ_TIMEOUT':.1,
        'decode_wevibe_info':lambda data: {
            'intensity':70,'internal_intensity':70,'external_intensity':0,
            'pattern':'wave','pattern_code':7,
        } if data == b'valid' else None,
        '_LOGGER':SimpleNamespace(debug=lambda *_args:None),
    }
    Bridge = extract_methods(BLE_SOURCE,'BleBridge',['_start_device_report','_poll_device_report'],namespace)
    b=Bridge()
    b._running=True;b._resetting=False;b._connections={}
    b._locks={}
    tasks=[]
    b._emit_safely=lambda message: listener(message,trace)
    def spawn(coro,label):
        t=asyncio.create_task(coro,name=label)
        tasks.append(t)
        return t
    b._spawn=spawn
    async def listener(message,history): history.append(message)
    services=SimpleNamespace(get_characteristic=lambda uuid:None)
    con=SimpleNamespace(address='11:22:33:44:55:66',connection_id='c1',client=SimpleNamespace(services=services,is_connected=True),report_task=None)
    b._connections[con.address]=con
    b._start_device_report(con)
    await asyncio.gather(*tasks)
    assert trace[-1]['status']=='unsupported',trace
    assert con.report_task is None
    trace.clear(); tasks.clear()
    char=SimpleNamespace(properties=['read'])
    con.client.services.get_characteristic=lambda uuid: char
    attempt=0
    async def reading(_):
        nonlocal attempt
        attempt+=1
        if attempt<=3:raise TimeoutError('dummy timeout')
        return b'valid'
    con.client.read_gatt_char=reading
    b._start_device_report(con)
    for _ in range(250):
        if any(e.get('type')=='device_report' for e in trace):break
        await sleeper(0.003)
    assert attempt>=4,attempt
    assert any(e.get('status')=='read_timeout' for e in trace), trace
    assert any(e.get('status')=='available' for e in trace),trace
    con.client.is_connected=False
    await sleeper(.02)
    for t in tasks:
        if not t.done():t.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)

    coord_ns = {"Any": object}
    Coordinator = extract_methods(COORD_SOURCE,'IntiHACoordinator',['_update_feedback_status'],coord_ns)
    coordinator=Coordinator()
    live=SimpleNamespace(connection_id=None,connected=False,feedback_status='disconnected',feedback_detail=None)
    coordinator.devices={con.address:live}
    coordinator._feedback_pending={}
    coordinator.enabled=True
    coordinator.engine_connected=True
    observed=[]
    coordinator._notify_intensity=lambda address:observed.append(address)
    coordinator._update_feedback_status({'address':con.address,'connection_id':'c1','status':'unsupported','detail':'Not found'})
    assert coordinator._feedback_pending[(con.address,'c1')]==('unsupported','Not found')
    live.connection_id='c1';live.connected=True
    coordinator._update_feedback_status({'address':con.address,'connection_id':'old','status':'available','detail':'stale'})
    assert live.feedback_status=='disconnected'
    coordinator._update_feedback_status({'address':con.address,'connection_id':'c1','status':'read_error','detail':'retrying'})
    assert live.feedback_status=='read_error'
    assert observed==[con.address]
    print('Standalone feedback tests passed: unsupported, transient 3-timeout recovery, session gating.')

asyncio.run(run())
