"""Every entity has a name and every enum option has a label, in both languages."""

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.aquastilla.binary_sensor import BINARY_SENSORS
from custom_components.aquastilla.sensor import SENSORS

FOLDER = Path(__file__).parent.parent / "custom_components" / "aquastilla" / "translations"
LANGUAGES = ("en", "pl")


def load(language: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FOLDER / f"{language}.json").read_text(encoding="utf-8"))
    return data


def flat_keys(node: Any, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    result: set[str] = set()
    for key, value in node.items():
        result |= flat_keys(value, f"{prefix}.{key}" if prefix else key)
    return result


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_sensor_has_a_name(language: str) -> None:
    sensors = load(language)["entity"]["sensor"]
    for description in SENSORS:
        assert description.translation_key in sensors, description.key
        assert sensors[description.translation_key]["name"], description.key


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_binary_sensor_has_a_name(language: str) -> None:
    sensors = load(language)["entity"]["binary_sensor"]
    for description in BINARY_SENSORS:
        assert description.translation_key in sensors, description.key
        assert sensors[description.translation_key]["name"], description.key


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_enum_option_has_a_label(language: str) -> None:
    sensors = load(language)["entity"]["sensor"]
    for description in SENSORS:
        for option in description.options or []:
            assert sensors[description.translation_key]["state"][option], (description.key, option)


def test_the_languages_have_the_same_keys() -> None:
    english, polish = flat_keys(load("en")), flat_keys(load("pl"))
    assert english == polish, sorted(english ^ polish)


def test_translation_keys_are_unique_per_platform() -> None:
    keys = [d.translation_key for d in SENSORS]
    assert len(keys) == len(set(keys))
    binary = [d.translation_key for d in BINARY_SENSORS]
    assert len(binary) == len(set(binary))
