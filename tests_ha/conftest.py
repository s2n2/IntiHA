"""Requires the real pinned Home Assistant runtime and its pytest plugin."""
import pytest

@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield
