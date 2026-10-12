"""The options flow: the poll interval, applied without reloading."""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla import poll_interval
from custom_components.aquastilla.const import (
    CONF_ENABLE_DEVICE_MANAGEMENT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.aquastilla.coordinator import AquastillaCoordinator

from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry


async def loaded(hass: HomeAssistant, backend: FakeBackend, **options: Any) -> MockConfigEntry:
    entry = make_entry()
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def open_options(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return result


# ---------------------------------------------------------------- the flow


async def test_the_form_defaults_to_five_minutes(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = await loaded(hass, backend)
    result = await open_options(hass, entry)
    schema = result["data_schema"].schema
    [key] = [k for k in schema if k == CONF_SCAN_INTERVAL]
    assert key.default() == 5


async def test_the_form_shows_the_saved_value(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = await loaded(hass, backend, scan_interval=20)
    result = await open_options(hass, entry)
    [key] = [k for k in result["data_schema"].schema if k == CONF_SCAN_INTERVAL]
    assert key.default() == 20


async def test_a_new_interval_applies_without_reloading(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    logins = len(backend.requests_to("POST", "/login-company"))

    result = await hass.config_entries.options.async_configure(
        (await open_options(hass, entry))["flow_id"], {CONF_SCAN_INTERVAL: 15}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 15, CONF_ENABLE_DEVICE_MANAGEMENT: False}
    assert entry.runtime_data is coordinator  # the same coordinator: not reloaded
    assert entry.state is ConfigEntryState.LOADED
    assert coordinator.update_interval == timedelta(minutes=15)
    assert coordinator.base_interval == timedelta(minutes=15)
    assert len(backend.requests_to("POST", "/login-company")) == logins  # no new login


async def test_changing_the_interval_restarts_the_running_timer(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    coordinator.async_add_listener(lambda: None)  # the timer only exists while someone listens
    with (
        patch.object(coordinator, "_async_unsub_refresh") as unsub,
        patch.object(coordinator, "_schedule_refresh") as schedule,
    ):
        coordinator.set_base_interval(timedelta(minutes=30))
    unsub.assert_called_once()
    schedule.assert_called_once()
    assert coordinator.update_interval == timedelta(minutes=30)  # read by the new timer


async def test_nothing_is_scheduled_when_nobody_listens(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    coordinator._listeners.clear()
    with patch.object(coordinator, "_schedule_refresh") as schedule:
        coordinator.set_base_interval(timedelta(minutes=30))
    schedule.assert_not_called()


async def test_a_running_backoff_timer_is_left_alone(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    coordinator.async_add_listener(lambda: None)
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await coordinator.async_refresh()
    assert coordinator.failures == 1
    with patch.object(coordinator, "_schedule_refresh") as schedule:
        coordinator.set_base_interval(timedelta(minutes=30))
    schedule.assert_not_called()  # the vendor's wait is not shortened
    assert coordinator.update_interval == timedelta(minutes=60)  # 30 min doubled


async def test_a_change_that_is_not_the_interval_does_not_touch_the_timer(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    with patch.object(coordinator, "set_base_interval") as apply:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "password": "a-new-one"},  # pragma: allowlist secret
        )
        await hass.async_block_till_done()
    apply.assert_not_called()


async def test_the_stored_interval_is_a_whole_number_of_minutes(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    result = await hass.config_entries.options.async_configure(
        (await open_options(hass, entry))["flow_id"], {CONF_SCAN_INTERVAL: 7.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert isinstance(entry.options[CONF_SCAN_INTERVAL], int)


@pytest.mark.parametrize("value", [0, -3, 61, 1000])
async def test_values_outside_the_range_are_refused_by_the_form(
    hass: HomeAssistant, backend: FakeBackend, value: int
) -> None:
    entry = await loaded(hass, backend)
    result = await open_options(hass, entry)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: value}
        )
    assert entry.options == {}


async def test_the_limits_themselves_are_accepted(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    for minutes in (1, 60):
        result = await open_options(hass, entry)
        done = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: minutes}
        )
        assert done["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.runtime_data.base_interval == timedelta(minutes=minutes)


# ---------------------------------------------------------------- at setup


async def test_a_saved_interval_is_used_when_the_entry_loads(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend, scan_interval=20)
    assert entry.runtime_data.update_interval == timedelta(minutes=20)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_SCAN_INTERVAL),
        ("soon", DEFAULT_SCAN_INTERVAL),
        ([], DEFAULT_SCAN_INTERVAL),
        (float("nan"), DEFAULT_SCAN_INTERVAL),
        (0, MIN_SCAN_INTERVAL),
        (-5, MIN_SCAN_INTERVAL),
        (0.1, MIN_SCAN_INTERVAL),
        (7, timedelta(minutes=7)),
        ("12", timedelta(minutes=12)),
        (9999, MAX_SCAN_INTERVAL),
    ],
)
def test_poll_interval_is_read_defensively(raw: Any, expected: timedelta) -> None:
    entry = MockConfigEntry(
        domain="aquastilla", options={} if raw is None else {CONF_SCAN_INTERVAL: raw}
    )
    assert poll_interval(entry) == expected


async def test_a_failure_backoff_is_measured_from_the_new_interval(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    coordinator: AquastillaCoordinator = entry.runtime_data
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(minutes=10)  # 5 min doubled
    coordinator.set_base_interval(timedelta(minutes=15))
    assert coordinator.update_interval == timedelta(minutes=30)  # 15 min doubled


async def test_device_management_is_off_by_default_and_can_be_switched_on(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await loaded(hass, backend)
    result = await open_options(hass, entry)
    assert CONF_ENABLE_DEVICE_MANAGEMENT in result["data_schema"].schema
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_ENABLE_DEVICE_MANAGEMENT: True}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_ENABLE_DEVICE_MANAGEMENT] is True
