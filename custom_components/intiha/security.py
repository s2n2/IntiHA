"""Transport security policy, not a device command implementation.

An ATT authentication/encryption rejection is different from an ESPHome API
login failure or a generic HCI disconnect. Only explicit ATT security errors
teach the bridge to request pairing. The Bond exception is an intentionally
narrow policy based on its observed encryption requirement, NOT a claim that
Buttplug specifies pairing for every We-Vibe device.
"""

from __future__ import annotations

_SECURITY_MARKERS = (
    "insufficient encryption",
    "insufficient authentication",
    "esp_gatt_insuf_encryption",
    "esp_gatt_insuf_authentication",
)


def is_gatt_security_error(error: BaseException) -> bool:
    """Recognise explicit ATT security rejection, including wrapped exceptions.

    Do not classify numeric error 15 by itself: HCI, ATT and API errors have
    different namespaces. bleak-esphome preserves the descriptive ATT error
    text and the original BluetoothGATTAPIError in its exception chain.
    """
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if any(marker in str(current).casefold() for marker in _SECURITY_MARKERS):
            return True
        # aioesphomeapi's typed error contains BluetoothGATTError.error.
        if type(current).__name__ == "BluetoothGATTAPIError":
            code = getattr(getattr(current, "error", None), "error", None)
            if code in (5, 15):
                return True
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return False


def profile_requests_pairing(name: str, protocols: object) -> bool:
    """Pair a positively identified We-Vibe Bond before protected GATT access.

    This is a local IntiHA policy motivated by an observed ATT 0x0F from Bond.
    Never select motor packets or infer a protocol from the advertised name.
    Other devices are learned only on explicit ATT security rejection.
    """
    return (
        name.strip().casefold() == "bond"
        and isinstance(protocols, (list, tuple))
        and "wevibe-8bit" in protocols
    )
