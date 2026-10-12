"""The entity table in the README says what the integration really creates."""

import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.const import DOMAIN

from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

README = Path(__file__).parent.parent / "README.md"
ROW = re.compile(r"^\| `(\w+)` \| `(\w+)` \| .* \| (on|off) \|$", re.MULTILINE)


def documented() -> dict[tuple[str, str], bool]:
    """(platform, key) -> enabled by default, from the table."""
    return {
        (platform, key): state == "on" for platform, key, state in ROW.findall(README.read_text())
    }


async def test_the_table_lists_every_entity_and_only_those(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    registry = er.async_get(hass)
    actual: dict[tuple[str, str], bool] = {}
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        assert item.platform == DOMAIN
        key = item.unique_id.removeprefix(f"{DEVICE_UUID}_")
        actual[(item.domain, key)] = item.disabled_by is None
    assert documented() == actual


def test_the_readme_names_the_actions_that_exist() -> None:
    text = README.read_text()
    for action in (
        "rename_device",
        "start_remote_service",
        "end_remote_service",
        "pair_device",
        "delete_device",
    ):
        assert f"aquastilla.{action}" in text
