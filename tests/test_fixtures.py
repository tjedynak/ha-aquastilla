"""Fixture hygiene: synthetic data only, nothing that identifies a real device."""

import ipaddress
import json
import re

import pytest

from .common import FIXTURES, load_fixture

JSON_FILES = sorted(FIXTURES.rglob("*.json"))  # the synthetic ones and the captured ones

JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MAC = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", re.IGNORECASE)
OBJECT_ID = re.compile(r"\b[0-9a-f]{24}\b", re.IGNORECASE)
DATE = re.compile(r"\b(\d{4}-\d\d-\d\d)T")
DATE_WINDOW_START = "2025-01-01"
DATE_WINDOW_END = "2026-07-31"
COORDINATE = re.compile(r'"(?:latitude|longitude)":\s*(-?[\d.]+)')
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOC_NET = ipaddress.ip_network("192.0.2.0/24")


def test_there_are_fixtures() -> None:
    assert JSON_FILES


def test_readme_declares_fixtures_synthetic() -> None:
    assert "synthetic" in (FIXTURES / "README.md").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda p: p.name)
def test_fixture_is_valid_json(path: object) -> None:
    json.loads(path.read_text(encoding="utf-8"))  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda p: p.name)
def test_no_real_identifiers(path: object) -> None:
    text = path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert not JWT.findall(text), "JWT-shaped string"
    assert all(u.lower().startswith("00000000-") for u in UUID.findall(text)), (
        "non-placeholder uuid"
    )
    assert all(e.endswith("@example.test") for e in EMAIL.findall(text)), "non-placeholder e-mail"
    assert all(m.lower().startswith("00:00:5e:00:53:") for m in MAC.findall(text)), (
        "real-looking MAC"
    )
    assert all(ipaddress.ip_address(i) in DOC_NET for i in IPV4.findall(text)), (
        "non-documentation IP"
    )
    assert all(o.startswith("0" * 18) for o in OBJECT_ID.findall(text)), "real-looking object id"
    assert all(float(c) == 0.0 for c in COORDINATE.findall(text)), "real-looking coordinates"


@pytest.mark.parametrize("path", JSON_FILES, ids=lambda p: p.name)
def test_every_date_in_a_fixture_sits_in_the_invented_window(path: object) -> None:
    """Keys count too: an unshifted date key once gave the real capture day away.

    The window ends before the day the real capture was made, so a date that was not
    shifted lands outside it.
    """
    text = path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    for found in DATE.findall(text):
        assert DATE_WINDOW_START <= found <= DATE_WINDOW_END, found


def test_identifier_checks_catch_real_looking_values() -> None:
    """The guards above must reject what they exist to reject."""
    assert JWT.search("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln")  # pragma: allowlist secret
    assert not all(
        u.startswith("00000000-") for u in UUID.findall("9b2f4c1e-1a2b-4c3d-8e4f-0123456789ab")
    )
    assert not EMAIL.findall("user@example.test")[0].endswith("@gmail.com")
    assert not MAC.findall("a4:cf:12:9e:4b:70")[0].startswith("00:00:5e:00:53:")
    assert ipaddress.ip_address("172.16.40.7") not in DOC_NET


def test_captured_fixtures_say_where_they_come_from() -> None:
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8").lower()
    assert "captured" in readme
    assert "anonymis" in readme


def test_device_uuid_is_consistent_across_fixtures() -> None:
    uuid = load_fixture("device")["uuid"]
    assert load_fixture("device_all")[0]["uuid"] == uuid
    assert load_fixture("state")["uuid"] == uuid
    assert load_fixture("details")["defaultDeviceId"] == uuid
    assert load_fixture("ws_event_seen")["uuid"] == uuid
    assert load_fixture("ws_percentage")["uuid"].lower() == uuid


def test_percentage_uuid_differs_in_case_to_exercise_lowercasing() -> None:
    assert load_fixture("ws_percentage")["uuid"] != load_fixture("device")["uuid"]


def test_water_data_has_the_four_series_a_real_device_returns() -> None:
    data = load_fixture("water_data")
    assert set(data) == {
        "WATER_HOUR_DATA",
        "WATER_DAY_DATA",
        "WATER_MONTH_DATA",
        "WATER_12_MONTH_DATA",
    }
    assert {k: len(v["data"]) for k, v in data.items()} == {
        "WATER_HOUR_DATA": 24,
        "WATER_DAY_DATA": 7,
        "WATER_MONTH_DATA": 31,
        "WATER_12_MONTH_DATA": 12,
    }
    for name, series in data.items():
        assert set(series) == {"chartLine", "data", "dataType", "timestamp"}
        assert series["dataType"] == name
        assert all(isinstance(v, int | float) for v in series["data"])


def test_event_page_shape() -> None:
    page = load_fixture("event_pageable")
    assert page["unseen"] == sum(not e["seen"] for e in page["page"]["content"])
    assert {"eventId", "type", "seen", "title", "body", "date", "additionalData"} <= set(
        page["page"]["content"][0]
    )


def test_detached_error_carries_code_minus_three() -> None:
    assert load_fixture("error_device_detached")["code"] == -3
