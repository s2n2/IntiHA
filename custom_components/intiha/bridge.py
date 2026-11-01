"""Authenticated, bounded and generation-aware connection to the IntiHA engine.

This transport deliberately does not replay requests. A reconnected session
receives configuration and current advertisements, never previous output values.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import aiohttp

from .const import ADMIN_TIMEOUT, MAX_MESSAGE_BYTES, MAX_PENDING_REQUESTS


class BridgeError(Exception):
    """A command failed or its session ended."""


class EngineBridge:
    """One outgoing bridge session; callbacks are on the HA event loop."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        hello: Callable[[], dict[str, Any]],
        on_message: Callable[[dict[str, Any], int], Awaitable[None]],
        on_disconnect: Callable[[str], Awaitable[None]],
    ) -> None:
        self._session = session
        self._url = url
        self._token = token
        self._hello = hello
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._closed = False
        self._wake = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._restart_generation = 0
        self.epoch = 0
        self.connected = False

    async def run(self) -> None:
        """Keep the transport connected, without affecting integration setup."""
        if self._runner_task is not None:
            raise RuntimeError("The IntiHA bridge already has a connection runner")
        self._runner_task = asyncio.current_task()
        try:
            await self._run()
        finally:
            self._runner_task = None

    async def _run(self) -> None:
        delay = 1.0
        while not self._closed:
            error = "Engine disconnected"
            self._connection_task = asyncio.create_task(self._run_connection())
            try:
                await asyncio.shield(self._connection_task)
            except asyncio.CancelledError:
                # Reconnect/close cancels the connection task, whose context
                # manager alone closes the socket. Cancellation of the runner
                # itself must still propagate to its owner.
                if asyncio.current_task().cancelling():
                    await self._interrupt_connection()
                    raise
            except aiohttp.WSServerHandshakeError as err:
                error = (
                    "Authentication failed"
                    if err.status in (401, 403)
                    else f"Engine HTTP error {err.status}"
                )
            except aiohttp.ClientError, OSError, TimeoutError:
                error = "Cannot reach engine"
            except BridgeError as err:
                error = str(err)
            except KeyError, ValueError, TypeError:
                error = "Invalid engine response"
            finally:
                self._connection_task = None
                if self.connected:
                    delay = 1.0
                self.connected = False
                self._ws = None
                self.epoch += 1
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(
                            BridgeError("Engine session ended; command was not replayed")
                        )
                self._pending.clear()
                await self._on_disconnect(error)
            if self._closed:
                break
            try:
                async with asyncio.timeout(delay + random.uniform(0, delay / 4)):
                    await self._wake.wait()
            except TimeoutError:
                pass
            self._wake.clear()
            delay = min(delay * 2, 60.0)

    async def _run_connection(self) -> None:
        """Own one handshake, receiver and socket cleanup without concurrent close calls."""
        generation = self._restart_generation
        async with self._session.ws_connect(
            self._url,
            headers={"Authorization": f"Bearer {self._token}"},
            heartbeat=10,
            timeout=aiohttp.ClientWSTimeout(ws_receive=35, ws_close=5),
            max_msg_size=MAX_MESSAGE_BYTES,
        ) as ws:
            if self._closed or generation != self._restart_generation:
                return
            self._ws = ws
            self.epoch += 1
            epoch = self.epoch
            await self.send(self._hello(), epoch=epoch)
            async for message in ws:
                if self._closed or generation != self._restart_generation:
                    break
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(message.data)
                    except (ValueError, TypeError) as err:
                        raise BridgeError("Invalid engine response") from err
                    if not isinstance(data, dict):
                        raise BridgeError("Invalid engine response")
                    if data.get("type") == "hello":
                        if data.get("protocol") != 1:
                            raise BridgeError("Unsupported bridge protocol")
                        self.connected = True
                    if data.get("type") == "admin_result":
                        future = self._pending.pop(data.get("id"), None)
                        if future is not None and not future.done():
                            if data.get("ok") is True:
                                future.set_result(data.get("result") or {})
                            else:
                                # Server error text is not logged; it may contain
                                # device details. The coordinator sanitises it.
                                future.set_exception(
                                    BridgeError(str(data.get("error", "Command failed")))
                                )
                    else:
                        await self._on_message(data, epoch)
                elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
                if self._closed or generation != self._restart_generation:
                    break

    async def send(self, message: dict[str, Any], *, epoch: int | None = None) -> None:
        """Send only to the intended transport generation."""
        async with self._send_lock:
            ws = self._ws
            if (
                self._closed
                or ws is None
                or ws.closed
                or (epoch is not None and epoch != self.epoch)
            ):
                raise BridgeError("Engine is disconnected")
            try:
                async with asyncio.timeout(10):
                    await ws.send_json(message)
            except (aiohttp.ClientError, ConnectionError, TimeoutError) as err:
                raise BridgeError("Engine write failed") from err

    async def request(self, op: str, **payload: Any) -> dict[str, Any]:
        """Send one admin request. Timeouts are failures, never retries."""
        if not self.connected:
            raise BridgeError("Engine is disconnected")
        if len(self._pending) >= MAX_PENDING_REQUESTS:
            raise BridgeError("Engine is busy")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send({"type": "admin", "id": request_id, "op": op, **payload})
            async with asyncio.timeout(ADMIN_TIMEOUT):
                return await future
        except TimeoutError as err:
            raise BridgeError("Engine command timed out; its outcome is unknown") from err
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def reconnect(self) -> None:
        """Close the old session; the background runner makes a fresh one."""
        if self._closed:
            return
        self._restart_generation += 1
        self.connected = False
        self._ws = None
        self._wake.set()
        await self._interrupt_connection()

    async def close(self) -> None:
        """End the transport and prevent further reconnects."""
        self._closed = True
        self._restart_generation += 1
        self.connected = False
        self._ws = None
        self._wake.set()
        if asyncio.current_task() is self._connection_task:
            # The receiver checks this flag after its callback returns. It
            # cannot wait for the runner, which is itself awaiting this task.
            return
        await self._interrupt_connection()
        runner = self._runner_task
        if runner is not None and runner is not asyncio.current_task():
            # Do not return while a reconnect handshake or its cleanup can
            # still create a socket/timer. The runner sees _closed after its
            # disconnection callback, or is woken from backoff immediately.
            with suppress(asyncio.CancelledError):
                await asyncio.shield(runner)

    async def _interrupt_connection(self) -> None:
        task = self._connection_task
        if task is None or task is asyncio.current_task():
            return
        if not task.done() and not task.cancelling():
            task.cancel()
        # Shield against a second caller cancelling the socket's __aexit__
        # while its first caller is already waiting for that same cleanup.
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(task)
