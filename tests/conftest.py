"""Shared pytest configuration."""

from collections.abc import AsyncGenerator

import aiohttp
import pytest
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

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


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    """A plain aiohttp session, closed after the test."""
    async with aiohttp.ClientSession() as client:
        yield client


@pytest.fixture
def backend(fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """The fake backend, with the integration pointed at it."""
    monkeypatch.setattr("custom_components.aquastilla.const.BASE_URL", fake_backend.base_url)
    return fake_backend


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Always use Home Assistant's snapshot extension, so files live in tests/snapshots.

    The plugin overrides this fixture too, but which of the two overrides wins depends
    on the order the plugins are loaded, and that differs between machines. Without
    this, a runner looked in tests/__snapshots__ and reported the snapshot as missing.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
