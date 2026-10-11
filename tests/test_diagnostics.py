"""The diagnostics download is useful for a bug report and contains no identities."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.aquastilla.commands import CommandTracker
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.diagnostics import (
    REDACTED,
    async_get_config_entry_diagnostics,
    pseudonyms,
)
from custom_components.aquastilla.models import DeviceEventType

from .common import load_fixture
from .fake_backend import DEVICE_UUID, VALID_EMAIL, VALID_PASSWORD, FakeBackend
from .setup_helpers import make_entry, setup_entry


async def diagnostics_for(hass: HomeAssistant, backend: FakeBackend) -> tuple[dict[str, Any], Any]:
    entry = make_entry()
    await setup_entry(hass, entry)
    return await async_get_config_entry_diagnostics(hass, entry), entry


def sensitive_values(token: str) -> list[str]:
    device = load_fixture("device")
    return [
        DEVICE_UUID,
        DEVICE_UUID.upper(),
        device["name"],
        device["macAddress"],
        device["ipAddress"],
        device["brand"]["serviceUrl"],
        VALID_EMAIL,
        VALID_EMAIL.split("@")[0],
        VALID_PASSWORD,
        load_fixture("details")["userId"],
        token,
    ]


async def test_the_report_has_the_data_a_bug_report_needs(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    report, _ = await diagnostics_for(hass, backend)

    device = report["devices"]["device_1"]
    assert device["model"] == "SYNTHETIC DUO"
    assert device["state"]["salt_days"] == 41
    assert device["state"]["cycle"] == "Softening"
    assert device["current_states"] == {"current": "Softening", "next": "Brine_refill"}
    assert device["settings"]["water_hardness"] == 18
    assert device["settings"]["regeneration_time"] == "00:01:00"
    assert device["settings"]["timezone"] == "Europe/Warsaw"
    assert device["history"]["regeneration"] == "2026-01-02T00:01:00+00:00"
    assert report["coordinator"]["last_update_success"] is True
    assert report["coordinator"]["update_interval_seconds"] == 300
    assert report["coordinator"]["consecutive_failures"] == 0


async def test_no_identity_or_secret_appears_anywhere(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    report, entry = await diagnostics_for(hass, backend)
    token = entry.runtime_data.client.token
    assert token  # the check below is only worth anything with a real token to look for
    dump = json.dumps(report)
    for value in sensitive_values(token):
        assert value not in dump, value


async def test_the_entry_credentials_are_redacted(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    report, _ = await diagnostics_for(hass, backend)
    assert report["entry"]["data"] == {"email": REDACTED, "password": REDACTED}
    assert report["entry"]["title"] == REDACTED
    assert report["entry"]["unique_id"] == REDACTED
    assert set(report["entry"]) == {"title", "unique_id", "version", "state", "data"}


async def test_devices_are_listed_under_neutral_names(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    report, _ = await diagnostics_for(hass, backend)
    assert list(report["devices"]) == ["device_1"]


async def test_the_device_section_is_an_allow_list(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    """A field added to a model later must not reach the report unless someone adds it here."""
    report, _ = await diagnostics_for(hass, backend)
    assert set(report["devices"]["device_1"]) == {
        "model",
        "brand",
        "history",
        "salt_notifications",
        "flood_notifications",
        "state",
        "current_states",
        "settings",
    }


async def test_a_failed_poll_shows_the_exception_class_not_its_message(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator: AquastillaCoordinator = entry.runtime_data
    backend.fail_next("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await coordinator.async_refresh()

    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["coordinator"]["last_update_success"] is False
    assert report["coordinator"]["last_exception"] == "UpdateFailed"
    assert report["coordinator"]["consecutive_failures"] == 1
    assert report["coordinator"]["update_interval_seconds"] == 600  # backed off
    assert DEVICE_UUID not in json.dumps(report)


async def test_without_a_push_channel_those_sections_are_empty(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    report, _ = await diagnostics_for(hass, backend)
    assert report["pending_commands"] is None
    assert report["websocket"] is None


async def test_pending_commands_and_the_push_channel_are_reported(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator: AquastillaCoordinator = entry.runtime_data
    tracker = CommandTracker(coordinator, timeout=5)
    coordinator.command_tracker = tracker
    coordinator.websocket = SimpleNamespace(connected=True, malformed_frames=2)  # type: ignore[assignment]

    release = asyncio.Event()

    async def hang() -> None:
        await release.wait()

    task = asyncio.create_task(
        tracker.async_send(DEVICE_UUID, DeviceEventType.SET_VACATION_MODE, hang)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    report = await async_get_config_entry_diagnostics(hass, entry)
    release.set()
    await task

    assert report["pending_commands"] == {"device_1": ["SET_VACATION_MODE"]}
    assert report["websocket"] == {"connected": True, "malformed_frames": 2}
    assert DEVICE_UUID not in json.dumps(report)


def test_pseudonyms_follow_the_sorted_ids_whatever_the_input_order() -> None:
    assert pseudonyms(["bb", "aa", "cc"]) == {"aa": "device_1", "bb": "device_2", "cc": "device_3"}
    assert pseudonyms([]) == {}


async def test_a_note_and_a_custom_name_do_not_reach_the_report(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = {
        **load_fixture("device"),
        "note": "Boiler room, key under the pot",
        "name": "Aunt Zofia's",
    }
    backend.replace_json("/device/all", [device])
    backend.replace_json(f"/device/{DEVICE_UUID}", device)
    report, _ = await diagnostics_for(hass, backend)
    dump = json.dumps(report)
    assert "Boiler room" not in dump
    assert "Zofia" not in dump


async def test_an_entry_that_is_not_loaded_still_gives_a_report(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    """A failed or unloaded entry has no coordinator; that is when a report is wanted."""
    entry = make_entry()
    entry.add_to_hass(hass)

    report = await async_get_config_entry_diagnostics(hass, entry)

    assert report["entry"]["data"] == {"email": REDACTED, "password": REDACTED}
    assert report["entry"]["title"] == REDACTED
    assert report["coordinator"] is None
    assert report["devices"] == {}
    assert report["pending_commands"] is None
    assert report["websocket"] is None
    assert VALID_PASSWORD not in json.dumps(report)
