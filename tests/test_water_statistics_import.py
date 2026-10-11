"""Statistics import with a real recorder: once on the first run, never twice."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.aquastilla.water_statistics import WaterStatistics, statistic_id

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, run_setup, setup_entry

START = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: object, enable_custom_integrations: None
) -> None:
    """The recorder has to exist before ``hass``, so it comes first here."""


async def stored(hass: HomeAssistant) -> list[dict[str, Any]]:
    await async_wait_recording_done(hass)
    sid = statistic_id(DEVICE_UUID)
    result = await get_instance(hass).async_add_executor_job(
        lambda: statistics_during_period(hass, START, None, {sid}, "hour", None, {"sum", "state"})
    )
    return [dict(row) for row in result.get(sid, [])]


def use_litres(backend: FakeBackend) -> None:
    backend.replace_json(
        f"/device/{DEVICE_UUID}/settings", {**load_fixture("settings"), "unitOfVolume": "l"}
    )


async def test_the_history_is_imported_on_the_first_run(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    use_litres(backend)
    await setup_entry(hass, make_entry())
    rows_ = await stored(hass)
    assert rows_
    sums = [r["sum"] for r in rows_]
    assert sums == sorted(sums)
    # Every litre of the fixture's hours (12 + 40 + 18 + 6 + 22 + 35) is in the total.
    assert sums[-1] >= 133.0
    assert [r["state"] for r in rows_] == sums
    assert any(path.endswith("/year/water_data") for path in (r.path for r in backend.requests))


async def test_a_restart_adds_nothing_and_does_not_read_the_year_again(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    use_litres(backend)
    entry = make_entry()
    await setup_entry(hass, entry)
    first = await stored(hass)
    backend.requests.clear()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await run_setup(hass, entry)
    second = await stored(hass)

    assert second == first
    assert not [r for r in backend.requests if r.path.endswith("/year/water_data")]


async def test_a_new_hour_appends_only_the_new_hour(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    use_litres(backend)
    entry = make_entry()
    await setup_entry(hass, entry)
    first = await stored(hass)

    water = load_fixture("water_data")
    hours = water["WATER_HOUR_DATA"]
    hours["data"] = [4.0, 9.0, *hours["data"][:-2]]  # the hour that was running is final: 9
    hours["timestamp"] = "2026-01-02T21:00:00.000+00:00"
    backend.replace_json(f"/device/{DEVICE_UUID}/water_data", water)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await run_setup(hass, entry)

    second = await stored(hass)
    assert len(second) == len(first) + 1
    assert second[:-1] == first
    assert second[-1]["sum"] == first[-1]["sum"] + 9.0


async def test_a_failing_year_read_is_retried_with_the_next_hour_not_the_next_poll(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    use_litres(backend)
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/year/water_data", status=500)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await stored(hass) == []
    attempts = [r for r in backend.requests if r.path.endswith("/year/water_data")]
    assert len(attempts) >= 1
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    after = [r for r in backend.requests if r.path.endswith("/year/water_data")]
    assert len(after) == len(attempts)


async def test_overlapping_updates_do_not_write_twice(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    use_litres(backend)
    entry = make_entry()
    await setup_entry(hass, entry)
    first = await stored(hass)

    statistics = WaterStatistics(hass, entry.runtime_data)
    await asyncio.gather(statistics.async_update(), statistics.async_update())

    # A fresh importer knows nothing, so it reads the recorder, finds every bucket
    # already stored and writes none.
    assert await stored(hass) == first


async def test_odd_data_for_one_device_does_not_stop_the_import(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_litres(backend)
    entry = make_entry()
    await setup_entry(hass, entry)

    def broken(*_: object) -> None:
        raise OverflowError

    monkeypatch.setattr("custom_components.aquastilla.water_statistics.plan", broken)
    statistics = WaterStatistics(hass, entry.runtime_data)
    await statistics.async_update()  # must not raise


async def test_an_unreadable_recorder_is_not_fatal(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_litres(backend)
    entry = make_entry()
    await setup_entry(hass, entry)
    first = await stored(hass)

    def unreadable(*_: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(
        "homeassistant.components.recorder.statistics.get_last_statistics", unreadable
    )
    await WaterStatistics(hass, entry.runtime_data).async_update()
    assert await stored(hass) == first
