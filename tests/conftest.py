"""Shared pytest configuration."""

from collections.abc import AsyncGenerator

import pytest

from .fake_backend import FakeBackend


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components/."""


@pytest.fixture
async def fake_backend(socket_enabled: None) -> AsyncGenerator[FakeBackend]:
    """A running fake vendor backend on loopback.

    Fails the test if it called an endpoint that must never run in tests, such
    as the water shut-off.
    """
    backend = FakeBackend()
    await backend.start()
    try:
        yield backend
    finally:
        await backend.stop()
    assert not backend.forbidden, f"test called forbidden endpoints: {backend.forbidden}"
