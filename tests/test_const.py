"""Invariants on the shared constants."""

from custom_components.aquastilla import const


def test_domain_matches_package_directory() -> None:
    assert const.DOMAIN == "aquastilla"


def test_default_interval_respects_minimum() -> None:
    assert const.DEFAULT_SCAN_INTERVAL >= const.MIN_SCAN_INTERVAL


def test_default_interval_is_conservative() -> None:
    # The backend is third-party production infrastructure; see CLAUDE.md.
    assert const.DEFAULT_SCAN_INTERVAL.total_seconds() >= 300
