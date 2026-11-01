"""Use Home Assistant's real integration loader during tests."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow this custom integration in the HA test runtime."""
