"""A bounded Buttplug v3 WebSocket client, independent of HA and Bluetooth.

This is an application client, not a replacement server or BLE protocol driver.
It negotiates v3 explicitly even with newer Intiface releases. One reader owns
responses/events; no request callback is allowed to wait inside the reader.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import logging

import aiohttp

from .models import DisconnectedError, ProtocolError, ServerError, integer, normalise_url

_LOGGER = logging.getLogger(__name__)


class EngineClient:
    def __init__(self, session: aiohttp.ClientSession, *, timeout: float = 8.0,
                 on_event: Callable[[str, dict], None] | None = None):
        self.session = session
        self.timeout = timeout
        self.on_event = on_event or (lambda _kind, _body: None)
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.reader: asyncio.Task | None = None
        self.pinger: asyncio.Task | None = None
        self.closed = asyncio.Event()
        self.closed.set()
        self.close_reason = "not_connected"
        self.close_code: int | None = None
        self.server_name = ""
        self.max_ping_time = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._message_id = 0
        self._send_lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed and not self.closed.is_set()

    async def open(self, url: str) -> dict:
        if self.ws is not None:
            raise RuntimeError("Use a fresh EngineClient for each server session")
        url = normalise_url(url)
        async with asyncio.timeout(self.timeout):
            self.ws = await self.session.ws_connect(url, autoping=True, max_msg_size=524288)
        self.closed.clear()
        self.close_reason = "connected"
        self.reader = asyncio.create_task(self._read(), name="intiha-native-reader")
        try:
            body = await self.request("RequestServerInfo", {
                "ClientName": "IntiHA native client", "MessageVersion": 3,
            }, expected="ServerInfo")
            if body.get("MessageVersion") != 3:
                raise ProtocolError("Server did not negotiate Buttplug message version 3")
            self.server_name = str(body.get("ServerName", "Intiface"))[:512]
            self.max_ping_time = integer(body.get("MaxPingTime", 0), "MaxPingTime")
            if self.max_ping_time and self.max_ping_time < 100:
                raise ProtocolError("Server heartbeat below 100ms is unsuitable for HA")
            self.pinger = asyncio.create_task(self._ping(), name="intiha-native-ping")
            return body
        except BaseException:
            await self.close("handshake_failed")
            raise

    async def request(self, kind: str, body: dict | None = None, *, expected: str = "Ok",
                      timeout: float | None = None) -> dict:
        if not self.connected:
            raise DisconnectedError(self.close_reason)
        if len(self._pending) >= 128:
            raise ProtocolError("Too many outstanding Intiface requests")
        self._message_id += 1
        message_id = self._message_id
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            # Bound lock acquisition, socket backpressure AND the reply. Lock
            # only the byte send, never the response wait.
            async with asyncio.timeout(self.timeout if timeout is None else timeout):
                async with self._send_lock:
                    if not self.connected:
                        raise DisconnectedError(self.close_reason)
                    assert self.ws is not None
                    await self.ws.send_json([{kind: {**(body or {}), "Id": message_id}}])
                response_kind, response = await future
            if response_kind == "Error":
                raise ServerError(str(response.get("ErrorMessage", "Intiface rejected request")),
                                  response.get("ErrorCode"))
            if response_kind != expected:
                raise ProtocolError(f"Expected {expected}, received {response_kind}")
            return response
        except TimeoutError as err:
            # A timed-out output may have been applied. Never retry it.
            await self.close(f"request_timeout:{kind}")
            raise DisconnectedError(f"Intiface did not acknowledge {kind}; outcome unknown") from err
        finally:
            self._pending.pop(message_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve exceptions even on send/cancel races.

    async def _read(self) -> None:
        assert self.ws is not None
        try:
            async for frame in self.ws:
                if frame.type == aiohttp.WSMsgType.TEXT:
                    try:
                        messages = json.loads(frame.data)
                    except (ValueError, TypeError) as err:
                        raise ProtocolError("Malformed JSON from Intiface") from err
                    if not isinstance(messages, list) or len(messages) > 512:
                        raise ProtocolError("Intiface frame must be a bounded message array")
                    for message in messages:
                        if not isinstance(message, dict) or len(message) != 1:
                            raise ProtocolError("Invalid Intiface message")
                        kind, body = next(iter(message.items()))
                        if not isinstance(body, dict):
                            raise ProtocolError("Invalid Intiface message body")
                        ident = integer(body.get("Id"), "message Id")
                        if ident != 0:
                            pending = self._pending.get(ident)
                            if pending is not None and not pending.done():
                                pending.set_result((kind, body))
                            # Unmatched/late replies must not become device events.
                            continue
                        if kind == "Error":
                            raise ServerError(str(body.get("ErrorMessage", "Server session error")),
                                              body.get("ErrorCode"))
                        self.on_event(kind, body)
                elif frame.type == aiohttp.WSMsgType.ERROR:
                    raise DisconnectedError(str(self.ws.exception() or "WebSocket failure"))
                elif frame.type == aiohttp.WSMsgType.BINARY:
                    raise ProtocolError("Unexpected binary Buttplug frame")
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.close_reason = f"{type(err).__name__}: {err}"
        finally:
            self.close_code = self.ws.close_code
            if self.close_reason == "connected":
                self.close_reason = "server_closed"
            self.closed.set()
            for pending in tuple(self._pending.values()):
                if not pending.done():
                    pending.set_exception(DisconnectedError(self.close_reason))
            if not self.ws.closed:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await self.ws.close()

    async def _ping(self) -> None:
        # Protocol Ping is distinct from WebSocket ping/pong.
        interval = min(15.0, self.max_ping_time / 3000) if self.max_ping_time else 15.0
        try:
            while self.connected:
                await asyncio.sleep(interval)
                await self.request("Ping", timeout=min(self.timeout, interval))
        except asyncio.CancelledError:
            raise
        except Exception as err:
            await self.close(f"heartbeat_failed:{type(err).__name__}:{err}")

    async def close(self, reason: str = "intentional_disconnect") -> None:
        # Cleanup outlives cancellation of a request or HA service call. A second
        # caller joins the same close rather than cancelling it halfway through.
        if self._close_task is None:
            caller = asyncio.current_task()
            self._close_task = asyncio.create_task(self._close_impl(reason, caller),
                                                   name="intiha-native-close")
        await asyncio.shield(self._close_task)

    async def _close_impl(self, reason: str, caller: asyncio.Task | None) -> None:
        self.close_reason = reason
        self.closed.set()
        for pending in tuple(self._pending.values()):
            if not pending.done():
                pending.set_exception(DisconnectedError(reason))
        current = asyncio.current_task()
        for task in (self.pinger, self.reader):
            if task is not None and task is not current and task is not caller:
                task.cancel()
        if self.ws is not None and not self.ws.closed:
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await self.ws.close()
        for task in (self.pinger, self.reader):
            if task is not None and task is not current and task is not caller:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        if self.ws is not None:
            self.close_code = self.ws.close_code
