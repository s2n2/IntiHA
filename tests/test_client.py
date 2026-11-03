import asyncio
import pytest
from intiha_core.client import EngineClient
from intiha_core.models import DisconnectedError, ServerError
from conftest import eventually


async def test_standard_handshake_and_inventory(native_server, session):
    server = await native_server()
    client = EngineClient(session, timeout=0.5)
    try:
        info = await client.open(server.url)
        assert info['MessageVersion'] == 3
        assert server.of_kind('RequestServerInfo')[0]['ClientName'] == 'IntiHA native client'
        assert await client.request('RequestDeviceList', expected='DeviceList') == {'Id': 2, 'Devices': []}
    finally:
        await client.close()


async def test_request_matching_with_out_of_order_responses(native_server, session):
    server = await native_server()
    async def delayed(ws, body):
        await asyncio.sleep(0.04 if body.get('Order') == 1 else 0.001)
        await ws.send_json([{'Ok': {'Id': body['Id'], 'Order': body['Order']}}])
        return True
    server.hooks['Test'] = delayed
    client = EngineClient(session)
    await client.open(server.url)
    try:
        a, b = await asyncio.gather(client.request('Test', {'Order': 1}), client.request('Test', {'Order': 2}))
        assert (a['Order'], b['Order']) == (1, 2)
    finally:
        await client.close()


async def test_server_error_is_not_retried(native_server, session):
    server = await native_server()
    async def reject(ws, body):
        await ws.send_json([{'Error': {'Id': body['Id'], 'ErrorMessage': 'Insufficient encryption', 'ErrorCode': 4}}])
        return True
    server.hooks['ScalarCmd'] = reject
    client = EngineClient(session)
    await client.open(server.url)
    try:
        with pytest.raises(ServerError, match='encryption'):
            await client.request('ScalarCmd')
        assert len(server.of_kind('ScalarCmd')) == 1
    finally:
        await client.close()


async def test_protocol_ping_sent(native_server, session):
    server = await native_server()
    server.ping_ms = 300
    client = EngineClient(session, timeout=0.5)
    await client.open(server.url)
    try:
        await eventually(lambda: server.of_kind('Ping'))
    finally:
        await client.close()


async def test_timeout_closes_session_and_no_replay(native_server, session):
    server = await native_server()
    async def ignore(ws, body):
        return True
    server.hooks['ScalarCmd'] = ignore
    client = EngineClient(session, timeout=0.05)
    await client.open(server.url)
    with pytest.raises(DisconnectedError, match='outcome unknown'):
        await client.request('ScalarCmd')
    assert not client.connected
    assert len(server.of_kind('ScalarCmd')) == 1
    await client.close()


async def test_bad_json_disconnects(native_server, session):
    server = await native_server()
    client = EngineClient(session)
    await client.open(server.url)
    await server.sockets[-1].send_str('{bad')
    await eventually(lambda: client.closed.is_set())
    assert 'Malformed JSON' in client.close_reason
    await client.close()


async def test_disconnect_unblocks_waiting_request(native_server, session):
    server = await native_server()
    async def ignore(ws, body): return True
    server.hooks['Test'] = ignore
    client = EngineClient(session)
    await client.open(server.url)
    pending = asyncio.create_task(client.request('Test'))
    await eventually(lambda: server.of_kind('Test'))
    await server.disconnect()
    with pytest.raises(DisconnectedError):
        await pending
    await client.close()


async def test_zero_watchdog_does_not_invent_protection(native_server, session):
    server = await native_server(); server.ping_ms = 0
    client = EngineClient(session)
    await client.open(server.url)
    assert client.max_ping_time == 0
    await client.close()


async def test_late_reply_not_dispatched_as_event(native_server, session):
    server = await native_server()
    events = []
    client = EngineClient(session, on_event=lambda k, b: events.append(k))
    await client.open(server.url)
    await server.sockets[-1].send_json([{'DeviceList': {'Id': 999999, 'Devices': []}}])
    await asyncio.sleep(0.02)
    assert events == []
    await client.close()


async def test_send_lock_wait_is_bounded(native_server, session):
    server = await native_server()
    client = EngineClient(session, timeout=0.05)
    await client.open(server.url)
    await client._send_lock.acquire()
    try:
        with pytest.raises(DisconnectedError, match="outcome unknown"):
            await asyncio.wait_for(client.request("Test"), 1)
        assert not server.of_kind("Test")
        assert not client.connected
    finally:
        client._send_lock.release()
        await client.close()


async def test_cancelled_request_is_never_sent_again(native_server, session):
    server = await native_server()
    async def ignore(ws, body):
        return True
    server.hooks["Test"] = ignore
    client = EngineClient(session)
    await client.open(server.url)
    pending = asyncio.create_task(client.request("Test"))
    await eventually(lambda: server.of_kind("Test"))
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(server.of_kind("Test")) == 1
    assert not client._pending
    await client.close()
