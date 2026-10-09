"""Fixture hygiene: synthetic data only, nothing that identifies a real device."""

import ipaddress
import json
import re

import pytest

from .common import FIXTURES, load_fixture

JSON_FILES = sorted(FIXTURES.glob("*.json"))

JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MAC = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", re.IGNORECASE)
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


def test_identifier_checks_catch_real_looking_values() -> None:
    """The guards above must reject what they exist to reject."""
    assert JWT.search("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln")  # pragma: allowlist secret
    assert not all(
        u.startswith("00000000-") for u in UUID.findall("9b2f4c1e-1a2b-4c3d-8e4f-0123456789ab")
    )
    assert not EMAIL.findall("user@example.test")[0].endswith("@gmail.com")
    assert not MAC.findall("00:00:5E:00:53:01")[0].startswith("00:00:5e:00:53:")
    assert ipaddress.ip_address("192.0.2.10") not in DOC_NET


def test_device_uuid_is_consistent_across_fixtures() -> None:
    uuid = load_fixture("device")["uuid"]
    assert load_fixture("device_all")[0]["uuid"] == uuid
    assert load_fixture("state")["uuid"] == uuid
    assert load_fixture("details")["defaultDeviceId"] == uuid
    assert load_fixture("ws_event_seen")["uuid"] == uuid
    assert load_fixture("ws_percentage")["uuid"].lower() == uuid


def test_percentage_uuid_differs_in_case_to_exercise_lowercasing() -> None:
    assert load_fixture("ws_percentage")["uuid"] != load_fixture("device")["uuid"]


def test_water_data_has_all_eight_series() -> None:
    keys = set(load_fixture("water_data"))
    assert keys == {
        f"{kind}_{period}_DATA"
        for kind in ("WATER", "FLOW")
        for period in ("HOUR", "DAY", "MONTH", "YEAR")
    }
    for series in load_fixture("water_data").values():
        assert set(series) == {"data", "chartLine", "timestamp"}
        assert all(isinstance(v, int | float) for v in series["data"])


def test_event_page_shape() -> None:
    page = load_fixture("event_pageable")
    assert page["unseen"] == sum(not e["seen"] for e in page["page"]["content"])
    assert {"eventId", "type", "seen", "title", "body", "date", "additionalData"} <= set(
        page["page"]["content"][0]
    )


def test_detached_error_carries_code_minus_three() -> None:
    assert load_fixture("error_device_detached")["code"] == -3
