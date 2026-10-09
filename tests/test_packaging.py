"""The repository layout HACS expects, checked locally.

HACS validates this on GitHub only, and the repository is not published, so the same
rules are enforced here.
"""

import json
import re
from pathlib import Path

from homeassistant.const import __version__ as HA_VERSION

from custom_components.aquastilla.const import DOMAIN

ROOT = Path(__file__).parent.parent
INTEGRATIONS = sorted(
    p for p in (ROOT / "custom_components").iterdir() if p.is_dir() and p.name != "__pycache__"
)
HACS_KEYS = {
    "name", "content_in_root", "zip_release", "filename", "hacs", "country",
    "homeassistant", "persistent_directory", "render_readme",
}  # fmt: skip


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def test_exactly_one_integration_lives_under_custom_components() -> None:
    assert [p.name for p in INTEGRATIONS] == [DOMAIN]


def test_the_integration_has_its_manifest_and_init() -> None:
    folder = ROOT / "custom_components" / DOMAIN
    assert (folder / "manifest.json").is_file()
    assert (folder / "__init__.py").is_file()


def test_hacs_json_is_valid() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"]
    assert set(hacs) <= HACS_KEYS
    assert "content_in_root" not in hacs  # the integration sits under custom_components/


def test_minimum_home_assistant_is_not_newer_than_the_one_tested_against() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert version_tuple(hacs["homeassistant"]) <= version_tuple(HA_VERSION)


def test_manifest_version_is_semantic() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / DOMAIN / "manifest.json").read_text(encoding="utf-8")
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["domain"] == DOMAIN


def test_readme_exists_for_hacs_to_render() -> None:
    assert (ROOT / "README.md").stat().st_size > 0
