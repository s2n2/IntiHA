"""Exercise production coordinator methods without a Home Assistant runtime.

Only the selected state methods are loaded from the production AST. These are
isolated policy/persistence tests, not an integration-loader or radio test.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.intiha import security

ADDRESS = "AA:BB:CC:DD:EE:01"
SOURCE = Path(security.__file__).with_name("coordinator.py")


@pytest.fixture
def state_subject():
    source = ast.parse(SOURCE.read_text(encoding="utf-8"))
    device = next(
        node
        for node in source.body
        if isinstance(node, ast.ClassDef) and node.name == "DeviceState"
    )
    coordinator = next(
        node
        for node in source.body
        if isinstance(node, ast.ClassDef) and node.name == "IntiHACoordinator"
    )
    selected = {
        "_requires_pairing",
        "_require_pairing",
        "_persisted_state",
        "async_initialize",
        "_async_engine_message",
        "_register_device",
    }
    coordinator.body = [
        node
        for node in coordinator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in selected
    ]
    assert {node.name for node in coordinator.body} == selected
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, device, coordinator], type_ignores=[]))
    scope = {
        "__name__": __name__,
        "dataclass": dataclass,
        "field": field,
        "Any": Any,
        "callback": lambda method: method,
        "_MAC": re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$"),
        "MAX_KNOWN_DEVICES": 256,
        "profile_requests_pairing": security.profile_requests_pairing,
    }
    exec(compile(tree, str(SOURCE), "exec"), scope)
    subject = scope["IntiHACoordinator"]()
    subject.devices = {}
    subject.discovered = {}
    subject.enabled = True
    subject.stop_uncertain = False
    subject._uncertain_addresses = set()
    subject.bridge = SimpleNamespace(epoch=7)
    subject.ble = SimpleNamespace(async_start=AsyncMock())
    subject._store = SimpleNamespace(async_load=AsyncMock(return_value=None))
    subject._save = Mock()
    subject._notify = Mock()
    return subject


async def test_identified_bond_sets_policy_and_persists_it(state_subject):
    await state_subject._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "Bond", "protocols": ["wevibe-8bit"]},
        7,
    )
    assert state_subject._requires_pairing(ADDRESS)
    saved = state_subject._persisted_state()
    assert saved["devices"][ADDRESS]["pairing_required"] is True
    assert set(saved["devices"][ADDRESS]) == {"name", "messages", "pairing_required"}
    state_subject._save.assert_called_once()


async def test_learned_requirement_round_trips_through_store(state_subject):
    state_subject._register_device(ADDRESS, "Device")
    assert not state_subject._requires_pairing(ADDRESS)
    state_subject._require_pairing(ADDRESS)
    saved = state_subject._persisted_state()
    state_subject.devices = {}
    state_subject._store.async_load.return_value = saved
    await state_subject.async_initialize()
    assert state_subject._requires_pairing(ADDRESS)
    state_subject.ble.async_start.assert_awaited_once()


async def test_legacy_state_does_not_force_generic_pairing(state_subject):
    state_subject._store.async_load.return_value = {"devices": {ADDRESS: {"name": "Device"}}}
    await state_subject.async_initialize()
    assert not state_subject._requires_pairing(ADDRESS)


async def test_other_wevibe_is_not_forced_to_pair(state_subject):
    await state_subject._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "Nova", "protocols": ["wevibe"]},
        7,
    )
    assert not state_subject._requires_pairing(ADDRESS)


async def test_retired_engine_discovery_cannot_change_security_policy(state_subject):
    await state_subject._async_engine_message(
        {"type": "discovered", "address": ADDRESS, "name": "Bond", "protocols": ["wevibe-8bit"]},
        6,
    )
    assert not state_subject.devices
    state_subject._save.assert_not_called()
