"""Pure-Python classification of transient BLE availability failures.

This module does not require Home Assistant so its behaviour can be tested
without hardware or the Home Assistant test fixture.
"""

from __future__ import annotations


def classify_ble_failure(message: str) -> tuple[str, float]:
    """Return user-facing wait reason and minimum retry delay in seconds."""
    value = message.casefold()
    if "waiting for bluetooth recovery" in value:
        return "waiting_for_proxy", 15.0
    if (
        "no backend with an available connection slot" in value
        or "no connectable bluetooth adapter" in value
        or "no scanner currently" in value
    ):
        return "waiting_for_proxy", 60.0
    if "timeout waiting for connect response" in value or "timed out" in value:
        return "connection_timed_out", 45.0
    if "not connected" in value or "not reachable" in value:
        return "waiting_for_advertisement", 30.0
    return "retrying", 15.0
