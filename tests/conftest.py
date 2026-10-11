"""Shared pytest configuration."""

from collections.abc import AsyncGenerator

import aiohttp
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
