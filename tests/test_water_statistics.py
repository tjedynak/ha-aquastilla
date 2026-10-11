"""Long-term statistics: which buckets are imported, and that they are imported once."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.aquastilla import models
from custom_components.aquastilla.coordinator import DeviceData
from custom_components.aquastilla.water_statistics import (
    Bucket,
    litres_factor,
    plan,
    rows,
    statistic_id,
)

from .common import load_fixture
from .fake_backend import DEVICE_UUID


def device_data(*, unit: str | None = "l", **series: dict[str, Any]) -> DeviceData:
    """The device of the fixtures, with its consumption series and volume unit."""
    water = load_fixture("water_data")
    water.update(series)
    settings = {**load_fixture("settings"), "unitOfVolume": unit}
    return DeviceData(
        device=models.Device.from_json(load_fixture("device")),
        state=models.DeviceState.from_json(load_fixture("state")),
        settings=models.DeviceSettings.from_json(settings),
        current_states=models.CurrentStates.from_json(load_fixture("current_states")),
        water=models.parse_water_data(water),
    )


def series(values: Sequence[float | None], timestamp: str) -> dict[str, Any]:
    return {"data": values, "chartLine": 0.0, "dataType": "X", "timestamp": timestamp}


def by_start(buckets: list[Bucket]) -> dict[datetime, float]:
    return {b.start: b.litres for b in buckets}


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ------------------------------------------------------------------- planning


def test_the_id_is_a_valid_external_statistic_id() -> None:
    assert statistic_id(DEVICE_UUID) == "aquastilla:00000000_0000_4000_8000_0000000000aa_water"


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("l", 1.0), ("m3", 1000.0), ("M3", 1000.0), ("m³", 1000.0), (None, 1.0), ("gal", 1.0)],
)
def test_litres_factor(unit: str | None, factor: float) -> None:
    assert litres_factor(unit) == factor


def test_the_running_hour_is_not_imported_and_the_others_are_where_the_clock_puts_them() -> None:
    found = by_start(plan(device_data(), None, None))
    # 20:00 on the device in Warsaw in January is 19:00 UTC. That hour is still running.
    assert utc(2026, 1, 2, 19) not in found
    assert found[utc(2026, 1, 2, 18)] == 0.0  # bucket 1
    assert found[utc(2026, 1, 1, 22)] == 22.0  # bucket 21
    assert found[utc(2026, 1, 1, 21)] == 35.0  # bucket 22


def test_a_gap_in_the_series_is_left_out_and_a_zero_is_kept() -> None:
    hours = series([5.0, None, 0.0, 7.0], "2026-01-02T20:00:00.000+00:00")
    found = by_start(plan(device_data(WATER_HOUR_DATA=hours), None, None))
    hourly = {s: v for s, v in found.items() if s >= utc(2026, 1, 2, 15)}
    assert hourly == {utc(2026, 1, 2, 17): 0.0, utc(2026, 1, 2, 16): 7.0}


def test_nothing_before_or_at_the_newest_stored_row_is_planned() -> None:
    everything = plan(device_data(), None, None)
    newest_stored = utc(2026, 1, 2, 3)
    later = plan(device_data(), None, newest_stored)
    assert later
    assert all(b.start > newest_stored for b in later)
    assert by_start(later).items() <= by_start(everything).items()


def test_a_stored_row_for_the_last_hour_leaves_nothing_to_do() -> None:
    assert plan(device_data(), None, utc(2026, 1, 2, 18)) == []


def test_coarser_buckets_cover_only_what_the_finer_ones_do_not() -> None:
    found = plan(device_data(), None, None)
    starts = [b.start for b in found]
    assert starts == sorted(starts)
    assert len(starts) == len(set(starts))
    hourly_begin = utc(2026, 1, 1, 21) - timedelta(hours=1)  # bucket 23
    coarse = [s for s in starts if s.minute == 0 and s.hour == 23 and s < hourly_begin]
    # Local midnight in January is 23:00 UTC. A whole day must end before the hours begin.
    assert coarse
    assert all(s + timedelta(days=1) <= hourly_begin for s in coarse)


def test_days_use_the_local_calendar() -> None:
    days = series([9.0, 4.0, 3.0], "2026-01-02T20:00:00.000+00:00")
    found = by_start(plan(device_data(WATER_MONTH_DATA=days), None, None))
    # Yesterday started at midnight in Warsaw, which is 23:00 UTC the day before.
    # It ends before the hours begin only if it ends at or before 19:00 UTC minus 23 h.
    assert utc(2025, 12, 31, 23) in found or utc(2026, 1, 1, 23) in found


def test_months_before_the_days_come_from_the_twelve_month_series() -> None:
    months = series([1.0] + [10.0 * n for n in range(1, 12)], "2026-01-02T20:00:00.000+00:00")
    found = by_start(plan(device_data(WATER_12_MONTH_DATA=months), None, None))
    # The 31 daily buckets reach back to 3 December, so December is not whole any more:
    # it is left out and November, whole and before them, is the first month.
    assert utc(2025, 11, 30, 23) not in found
    assert found[utc(2025, 10, 31, 23)] == 20.0  # bucket 2, November
    assert found[utc(2025, 1, 31, 23)] == 110.0  # bucket 11, February


def test_the_previous_year_fills_what_the_twelve_months_do_not_reach() -> None:
    year = models.parse_year_water_data(
        {"2026-01-01T00:00:00.000+00:00": series([float(n) for n in range(1, 13)], "x")}
    )
    found = by_start(plan(device_data(), year, None))
    # The 12 month series reaches back to February 2025, so of the previous year only
    # January, the last of the twelve values, is still missing.
    assert found[utc(2024, 12, 31, 23)] == 12.0


def test_a_year_filed_under_something_that_is_not_a_date_is_ignored() -> None:
    year = models.parse_year_water_data({"not a date": series([5.0] * 12, "x")})
    assert plan(device_data(), year, None) == plan(device_data(), None, None)


def test_cubic_metres_are_stored_as_litres() -> None:
    in_litres = by_start(plan(device_data(unit="l"), None, None))
    in_cubic = by_start(plan(device_data(unit="m3"), None, None))
    assert in_cubic == {s: v * 1000 for s, v in in_litres.items()}


def test_without_any_series_nothing_is_planned() -> None:
    data = replace(device_data(), water={})
    assert plan(data, None, None) == []


def test_rows_run_on_from_the_base_and_never_go_down() -> None:
    first, second, third = utc(2026, 1, 1), utc(2026, 1, 1, 1), utc(2026, 1, 1, 2)
    out = rows([Bucket(first, 2.0), Bucket(second, -5.0), Bucket(third, 3.0)], 100.0)
    assert [r["sum"] for r in out] == [102.0, 102.0, 105.0]
    assert [r["state"] for r in out] == [102.0, 102.0, 105.0]
    assert [r["start"] for r in out] == [first, second, third]
