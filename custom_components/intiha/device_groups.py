"""Keep IntiHA device categories available as native Home Assistant labels.

The Home Assistant integration screen owns its device-list headings, so labels
are used for dynamic filtering without changing device names or unique IDs.
"""

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, label_registry as lr

from .const import DOMAIN

GROUP_LABELS = {
    "connected": ("IntiHA - Currently connected", "mdi:bluetooth-connect"),
    "known": ("IntiHA - Known, not connected", "mdi:bluetooth-off"),
    "disabled": ("IntiHA - Connection disabled", "mdi:bluetooth-disabled"),
}


def device_group(device) -> str:
    """Disabled has priority; all remaining devices split by live link state."""
    if not device.connect_enabled:
        return "disabled"
    return "connected" if device.connected else "known"


@callback
def sync_device_group(coordinator, address: str) -> None:
    """Maintain only our three labels; preserve all user-assigned labels."""
    entry_id = coordinator.entry.entry_id
    registry = dr.async_get(coordinator.hass)
    registered = registry.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}:{address}"), entry_id
    )
    if registered is None or address not in coordinator.devices:
        return

    labels = lr.async_get(coordinator.hass)
    category_ids = set()
    chosen = None
    category = device_group(coordinator.devices[address])
    for key, (name, icon) in GROUP_LABELS.items():
        existing = labels.async_get_label_by_name(name)
        if existing is None:
            existing = labels.async_create(name, icon=icon)
        category_ids.add(existing.label_id)
        if key == category:
            chosen = existing.label_id

    desired = (set(registered.labels) - category_ids) | {chosen}
    if desired != set(registered.labels):
        registry.async_update_device(registered.id, labels=desired)
