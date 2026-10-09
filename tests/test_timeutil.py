"""Timestamp and timezone handling: parsing, formatting, DST and device zones."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.aquastilla import timeutil

WARSAW = ZoneInfo("Europe/Warsaw")
NEW_YORK = ZoneInfo("America/New_York")
DAY = date(2026, 1, 2)


# ---------------------------------------------------------------- instants


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-02T07:34:00Z", datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        ("2026-01-02T07:34:00.123Z", datetime(2026, 1, 2, 7, 34, 0, 123000, tzinfo=UTC)),
        ("2026-01-02T08:34:00+01:00", datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        ("2026-01-02T07:34:00", datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        ("  2026-01-02T07:34:00Z ", datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        (1767339240, datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        (1767339240.0, datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
        (1767339240000, datetime(2026, 1, 2, 7, 34, tzinfo=UTC)),
    ],
)
def test_parse_timestamp_returns_aware_utc(raw: object, expected: datetime) -> None:
    result = timeutil.parse_timestamp(raw)
    assert result == expected
    assert result is not None
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "No data",
        "Brak danych",
        "2026-13-45T00:00:00Z",
        0,
        -5,
        True,
        False,
        [],
        {},
        1e30,
    ],
)
def test_missing_or_bad_input_becomes_none(raw: object) -> None:
    assert timeutil.parse_timestamp(raw) is None


def test_offset_is_normalised_to_utc() -> None:
    result = timeutil.parse_timestamp("2026-07-01T12:00:00+05:30")
    assert result is not None
    assert result.tzinfo is UTC
    assert result.hour == 6
    assert result.minute == 30


def test_to_vendor_timestamp_uses_utc_milliseconds() -> None:
    value = datetime(2026, 1, 2, 7, 34, 5, 123456, tzinfo=UTC)
    assert timeutil.to_vendor_timestamp(value) == "2026-01-02T07:34:05.123Z"


def test_to_vendor_timestamp_converts_other_zones() -> None:
    value = datetime(2026, 1, 2, 8, 34, tzinfo=WARSAW)
    assert timeutil.to_vendor_timestamp(value) == "2026-01-02T07:34:00.000Z"


def test_to_vendor_timestamp_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        timeutil.to_vendor_timestamp(datetime(2026, 1, 2, 7, 34))


def test_instant_round_trips() -> None:
    value = datetime(2026, 10, 25, 0, 30, 15, 250000, tzinfo=UTC)
    assert timeutil.parse_timestamp(timeutil.to_vendor_timestamp(value)) == value


# ------------------------------------------------------ wall-clock time


def test_regeneration_time_round_trips() -> None:
    encoded = timeutil.format_wall_clock(time(2, 30), on=DAY)
    assert encoded == "2026-01-02T02:30:00.000Z"
    assert timeutil.parse_wall_clock(encoded) == time(2, 30)


@pytest.mark.parametrize("hour", range(24))
def test_every_hour_round_trips(hour: int) -> None:
    value = time(hour, 1)
    assert timeutil.parse_wall_clock(timeutil.format_wall_clock(value, on=DAY)) == value


def test_parse_wall_clock_keeps_digits_and_ignores_date_and_label() -> None:
    assert timeutil.parse_wall_clock("1970-01-01T00:01:00.000Z") == time(0, 1)
    assert timeutil.parse_wall_clock("2031-06-30T23:59:00Z") == time(23, 59)


def test_parse_wall_clock_does_not_shift_by_offset() -> None:
    # The digits are the wall clock whatever label they carry.
    assert timeutil.parse_wall_clock("2026-01-02T02:30:00+02:00") == time(2, 30)


@pytest.mark.parametrize("raw", [None, "", "  ", "soon", 5, "2026-01-02T25:00:00Z"])
def test_parse_wall_clock_bad_input_is_none(raw: object) -> None:
    assert timeutil.parse_wall_clock(raw) is None


def test_format_wall_clock_drops_seconds() -> None:
    assert timeutil.format_wall_clock(time(2, 30, 45), on=DAY).endswith("T02:30:00.000Z")


# --------------------------------------------------------- device timezone


def test_device_zone_resolves_a_known_name() -> None:
    assert timeutil.device_zone("Europe/Warsaw", UTC) == WARSAW


@pytest.mark.parametrize("name", [None, "", "Mars/Olympus_Mons", 7, "../etc/passwd"])
def test_device_zone_falls_back(name: object) -> None:
    fallback = timezone(timedelta(hours=3))
    assert timeutil.device_zone(name, fallback) is fallback


def test_same_wall_clock_is_a_different_instant_in_a_different_zone() -> None:
    warsaw = timeutil.wall_clock_to_utc(time(2, 30), WARSAW, on=DAY)
    new_york = timeutil.wall_clock_to_utc(time(2, 30), NEW_YORK, on=DAY)
    assert warsaw == datetime(2026, 1, 2, 1, 30, tzinfo=UTC)
    assert new_york == datetime(2026, 1, 2, 7, 30, tzinfo=UTC)


def test_device_zone_differs_from_home_assistant_zone() -> None:
    """A device in New York seen from a Warsaw Home Assistant keeps its own clock."""
    instant = timeutil.wall_clock_to_utc(time(2, 30), NEW_YORK, on=DAY)
    assert timeutil.utc_to_wall_clock(instant, NEW_YORK) == time(2, 30)
    assert timeutil.utc_to_wall_clock(instant, WARSAW) == time(8, 30)


def test_utc_to_wall_clock_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        timeutil.utc_to_wall_clock(datetime(2026, 1, 2, 7, 34), WARSAW)


# ------------------------------------------------------------------- DST


def test_offset_changes_across_spring_forward() -> None:
    before = timeutil.wall_clock_to_utc(time(1, 30), WARSAW, on=date(2026, 3, 28))
    after = timeutil.wall_clock_to_utc(time(1, 30), WARSAW, on=date(2026, 3, 30))
    assert before.hour == 0  # CET, UTC+1
    assert after.hour == 23  # CEST, UTC+2, previous UTC day
    assert after.date() == date(2026, 3, 29)


def test_time_skipped_by_spring_forward_uses_the_offset_before_the_gap() -> None:
    # 02:30 does not exist on 2026-03-29 in Warsaw (clocks jump 02:00 -> 03:00).
    instant = timeutil.wall_clock_to_utc(time(2, 30), WARSAW, on=date(2026, 3, 29))
    assert instant == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert timeutil.utc_to_wall_clock(instant, WARSAW) == time(3, 30)


def test_ambiguous_time_in_fall_back_is_the_first_occurrence() -> None:
    # 02:30 happens twice on 2026-10-25 in Warsaw; the first is still CEST (UTC+2).
    instant = timeutil.wall_clock_to_utc(time(2, 30), WARSAW, on=date(2026, 10, 25))
    assert instant == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_unambiguous_times_round_trip_through_every_dst_day() -> None:
    day = date(2026, 1, 1)
    for offset in range(365):
        current = day + timedelta(days=offset)
        instant = timeutil.wall_clock_to_utc(time(4, 15), WARSAW, on=current)
        assert timeutil.utc_to_wall_clock(instant, WARSAW) == time(4, 15)


def test_different_dst_calendars_do_not_line_up() -> None:
    """Between 8 and 29 March the US is on summer time and Europe is not."""
    day = date(2026, 3, 15)
    gap = timeutil.wall_clock_to_utc(time(12, 0), WARSAW, on=day) - timeutil.wall_clock_to_utc(
        time(12, 0), NEW_YORK, on=day
    )
    assert gap == timedelta(hours=-5)
    assert gap != timeutil.wall_clock_to_utc(
        time(12, 0), WARSAW, on=DAY
    ) - timeutil.wall_clock_to_utc(time(12, 0), NEW_YORK, on=DAY)
