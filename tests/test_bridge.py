"""Bounded transport and session loss tests without a real network."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.intiha.bridge import BridgeError, EngineBridge
from custom_components.intiha.const import MAX_PENDING_REQUESTS


class FakeSocket:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.messages = asyncio.Queue()
        self.started = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send_json(self, message):
        self.sent.append(message)
        self.started.set()

    async def close(self):
        self.closed = True
        await self.messages.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.messages.get()
        if message is None:
            raise StopAsyncIteration
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=message)


@pytest.fixture
def transport():
    socket = FakeSocket()
    calls = []

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return socket

    on_message = AsyncMock()
    on_disconnect = AsyncMock()
    bridge = EngineBridge(
        SimpleNamespace(ws_connect=connect),
        "ws://engine:12346/bridge",
        lambda: {"type": "hello", "protocol": 1, "enabled": False},
        on_message,
        on_disconnect,
    )
    return bridge, socket, calls, on_message, on_disconnect


async def test_tokenless_connection_and_handshake_before_commands(transport):
    bridge, socket, calls, on_message, _ = transport
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    assert calls[0][0] == "ws://engine:12346/bridge"
    assert "headers" not in calls[0][1]
    assert socket.sent == [{"type": "hello", "protocol": 1, "enabled": False}]
    assert not bridge.connected
    await socket.messages.put('{"type":"hello","protocol":1}')
    for _ in range(5):
        await asyncio.sleep(0)
    assert bridge.connected
    on_message.assert_awaited_once()
    await bridge.close()
    await runner


async def test_in_flight_request_fails_on_session_loss_and_is_not_replayed(transport):
    bridge, socket, _, _, on_disconnect = transport
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    await socket.messages.put('{"type":"hello","protocol":1}')
    for _ in range(5):
        await asyncio.sleep(0)
    request = asyncio.create_task(bridge.request("command", message={"ScalarCmd": {}}))
    for _ in range(5):
        await asyncio.sleep(0)
    await bridge.close()
    await runner
    with pytest.raises(BridgeError, match="not replayed"):
        await request
    assert len(socket.sent) == 2
    assert not bridge._pending
    on_disconnect.assert_awaited_once()


async def test_response_is_correlated_to_request(transport):
    bridge, socket, _, _, _ = transport
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    await socket.messages.put('{"type":"hello","protocol":1}')
    for _ in range(5):
        await asyncio.sleep(0)
    request = asyncio.create_task(bridge.request("status"))
    for _ in range(5):
        await asyncio.sleep(0)
    await socket.messages.put('{"type":"admin_result","id":1,"ok":true,"result":{"enabled":false}}')
    assert await request == {"enabled": False}
    await bridge.close()
    await runner


async def test_old_epoch_cannot_write_to_new_socket(transport):
    bridge, socket, _, _, _ = transport
    bridge._ws = socket
    bridge.epoch = 9
    with pytest.raises(BridgeError, match="disconnected"):
        await bridge.send({"type": "response"}, epoch=8)
    assert not socket.sent


async def test_admin_waiting_for_send_lock_cannot_cross_reconnection(transport):
    bridge, old_socket, _, _, _ = transport
    bridge._ws = old_socket
    bridge.connected = True
    bridge.epoch = 1
    await bridge._send_lock.acquire()
    request = asyncio.create_task(bridge.request("command", message={"ScalarCmd": {}}))
    await asyncio.sleep(0)
    assert len(bridge._pending) == 1
    # Match the disconnect path while an old sender is waiting for the lock.
    for future in bridge._pending.values():
        future.set_exception(BridgeError("Engine session ended; command was not replayed"))
    bridge._pending.clear()
    new_socket = FakeSocket()
    bridge._ws = new_socket
    bridge.epoch = 2
    bridge._send_lock.release()

    with pytest.raises(BridgeError, match="disconnected"):
        await request

    assert not old_socket.sent
    assert not new_socket.sent
    assert not bridge._pending


async def test_pending_request_limit_fails_before_network(transport):
    bridge, socket, _, _, _ = transport
    bridge.connected = True
    bridge._pending = {
        index: asyncio.get_running_loop().create_future() for index in range(MAX_PENDING_REQUESTS)
    }
    with pytest.raises(BridgeError, match="busy"):
        await bridge.request("status")
    assert not socket.sent
    for future in bridge._pending.values():
        future.cancel()


async def test_admin_timeout_does_not_retry(transport, monkeypatch):
    bridge, socket, _, _, _ = transport
    bridge._ws = socket
    bridge.connected = True
    monkeypatch.setattr("custom_components.intiha.bridge.ADMIN_TIMEOUT", 0.001)
    with pytest.raises(BridgeError, match="outcome is unknown"):
        await bridge.request("stop")
    assert len(socket.sent) == 1
    assert not bridge._pending


async def test_unknown_protocol_fails_closed(transport):
    bridge, socket, _, _, on_disconnect = transport

    async def stop_retrying(_reason):
        bridge._closed = True

    on_disconnect.side_effect = stop_retrying
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    await socket.messages.put('{"type":"hello","protocol":99}')
    await runner
    assert not bridge.connected
    on_disconnect.assert_awaited_once_with("Unsupported bridge protocol")


async def test_close_owns_and_cancels_an_inflight_handshake(transport):
    bridge, _, _, _, on_disconnect = transport
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class PendingHandshake:
        async def __aenter__(self):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        async def __aexit__(self, *_args):
            pass

    bridge._session = SimpleNamespace(ws_connect=lambda *_args, **_kwargs: PendingHandshake())
    runner = asyncio.create_task(bridge.run())
    await entered.wait()
    await bridge.close()
    assert cancelled.is_set()
    assert runner.done()
    assert bridge._connection_task is None
    assert bridge._ws is None
    assert not bridge.connected
    on_disconnect.assert_awaited_once()


async def test_close_during_reconnect_waits_for_single_socket_cleanup(transport):
    bridge, _, _, _, _ = transport
    closing = asyncio.Event()
    release_close = asyncio.Event()

    class SlowCleanupSocket(FakeSocket):
        exits = 0

        async def __aexit__(self, *_args):
            self.exits += 1
            closing.set()
            await release_close.wait()
            self.closed = True

        async def close(self):
            pytest.fail("Only the connection task may close its socket")

    socket = SlowCleanupSocket()
    bridge._session = SimpleNamespace(ws_connect=lambda *_args, **_kwargs: socket)
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    reconnecting = asyncio.create_task(bridge.reconnect())
    await closing.wait()
    shutdown = asyncio.create_task(bridge.close())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not reconnecting.done()
    release_close.set()
    await asyncio.gather(reconnecting, shutdown)
    assert runner.done()
    assert socket.closed
    assert socket.exits == 1
    assert bridge._ws is None


async def test_close_from_receiver_callback_does_not_deadlock(transport):
    bridge, socket, _, on_message, _ = transport

    async def close_from_callback(_message, _epoch):
        await bridge.close()

    on_message.side_effect = close_from_callback
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    await socket.messages.put('{"type":"hello","protocol":1}')
    async with asyncio.timeout(1):
        await runner
    assert socket.closed
    assert bridge._ws is None


async def test_runner_cancellation_cannot_interrupt_socket_cleanup_twice(transport):
    bridge, _, _, _, _ = transport
    closing = asyncio.Event()
    release_close = asyncio.Event()

    class SlowCleanupSocket(FakeSocket):
        async def __aexit__(self, *_args):
            closing.set()
            await release_close.wait()
            self.closed = True

    socket = SlowCleanupSocket()
    bridge._session = SimpleNamespace(ws_connect=lambda *_args, **_kwargs: socket)
    runner = asyncio.create_task(bridge.run())
    await socket.started.wait()
    reconnecting = asyncio.create_task(bridge.reconnect())
    await closing.wait()
    runner.cancel()
    await asyncio.sleep(0)
    assert not runner.done()
    release_close.set()
    await reconnecting
    with pytest.raises(asyncio.CancelledError):
        await runner
    assert socket.closed
    assert bridge._connection_task is None
    assert bridge._runner_task is None
