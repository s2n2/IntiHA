"""The Handy's own commands (firmware 4 RPC, protobuf), for what Buttplug cannot do.

Buttplug drives The Handy with direct moves only (HDSP "go to X in T ms").
Ohdoki's documentation says direct mode is for manual control and "only works
when you want to move the Handy to a position without considering timing";
its built-in stroking (HAMP: a speed in % between the stroke zone's min and
max) is the mode for a remote operator. This encodes those few commands by
hand (no protobuf library): the message layout is the published
handy_rpc.proto / messages.proto that Buttplug itself compiles.

Ids start high so replies never collide with Buttplug's own requests.
"""

from __future__ import annotations

import itertools
import struct
from typing import Any

# GATT (thehandy-v3 in the Buttplug device configuration)
TX = "77835032-40f7-11ee-be56-0242ac120002"

MODE_HAMP = 0
MODE_HDSP = 2

# Request.params field numbers
_MODE_SET = 701
_MODE_GET = 700
_HAMP_START = 720
_HAMP_STOP = 721
_HAMP_VELOCITY = 723
_HAMP_STATE = 724
_HAMP_ZONE = 725
_HDSP_STOP = 746
_STROKE_GET = 840
_SLIDER_STATE = 842

_ids = itertools.count(40000)


def _varint(n: int) -> bytes:
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _uint(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _float(field: int, value: float) -> bytes:
    return _key(field, 5) + struct.pack("<f", value)


def _msg(field: int, body: bytes) -> bytes:
    return _key(field, 2) + _varint(len(body)) + body


def request(param: int, body: bytes = b"") -> tuple[int, bytes]:
    """One RpcMessage{type: REQUEST, request: {id, <param>: body}}; returns (id, bytes)."""
    rid = next(_ids)
    req = _uint(1, rid) + _msg(param, body)
    return rid, _uint(1, 1) + _msg(2, req)


def mode_set(mode: int) -> tuple[int, bytes]:
    return request(_MODE_SET, _uint(1, mode) if mode else b"")


def mode_get() -> tuple[int, bytes]:
    return request(_MODE_GET)


def hamp_zone(lo: float, hi: float) -> tuple[int, bytes]:
    """Stroke range for built-in stroking, 0-1 of the stroke zone."""
    return request(_HAMP_ZONE, _float(1, lo) + _float(2, hi))


def hamp_velocity(value: float) -> tuple[int, bytes]:
    return request(_HAMP_VELOCITY, _float(1, value))


def hamp_start() -> tuple[int, bytes]:
    return request(_HAMP_START)


def hamp_stop() -> tuple[int, bytes]:
    return request(_HAMP_STOP)


def hamp_state() -> tuple[int, bytes]:
    return request(_HAMP_STATE)


def hdsp_stop() -> tuple[int, bytes]:
    return request(_HDSP_STOP)


def stroke_get() -> tuple[int, bytes]:
    return request(_STROKE_GET)


def slider_state() -> tuple[int, bytes]:
    return request(_SLIDER_STATE)


# ------------------------------------------------------------- decoding replies
def _fields(data: bytes) -> list[tuple[int, int, Any]]:
    out, i = [], 0
    while i < len(data):
        key, i = _read_varint(data, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _read_varint(data, i)
        elif wire == 5:
            val = struct.unpack("<f", data[i:i + 4])[0]
            i += 4
        elif wire == 1:
            val = struct.unpack("<d", data[i:i + 8])[0]
            i += 8
        elif wire == 2:
            n, i = _read_varint(data, i)
            val = data[i:i + n]
            i += n
        else:
            raise ValueError("unsupported wire type")
        out.append((field, wire, val))
    return out


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def _tree(data: bytes, depth: int = 0) -> Any:
    """Best-effort decode: nested messages where they parse, else raw values."""
    try:
        out = []
        for field, wire, val in _fields(data):
            if wire == 2 and depth < 4:
                try:
                    val = _tree(val, depth + 1)
                except Exception:  # noqa: BLE001
                    val = val.hex(" ")
            elif isinstance(val, float):
                val = round(val, 4)
            out.append([field, val])
        return out
    except Exception:  # noqa: BLE001
        return data.hex(" ")


def describe(data: bytes) -> dict[str, Any]:
    """A reply or notification, readable: type, id and decoded fields."""
    fields = _fields(data)
    kind = next((v for f, w, v in fields if f == 1 and w == 0), None)
    body = next((v for f, w, v in fields if f in (4, 5) and w == 2), b"")
    names = {3: "response", 4: "notification"}
    out: dict[str, Any] = {"type": names.get(kind, kind), "raw": data.hex(" ")}
    try:
        out["fields"] = _tree(body)
    except Exception:  # noqa: BLE001
        pass
    for f, w, v in _fields(body) if body else []:
        if f == 1 and w == 0 and kind == 3:
            out["id"] = v
    return out
