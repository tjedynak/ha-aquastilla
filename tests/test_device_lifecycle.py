"""A device that leaves the account, comes back or is added later."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla.const import DEVICE_LIST_INTERVAL, DOMAIN
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.entity import AquastillaEntity

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

OTHER = "00000000-0000-4000-8000-0000000000bb"
STATE = f"/device/{DEVICE_UUID}/state"
DETACHED = load_fixture("error_device_detached")


@pytest.fixture
async def entry(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> MockConfigEntry:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    backend.requests.clear()
    return entry


def coordinator_of(entry: MockConfigEntry) -> AquastillaCoordinator:
    coordinator: AquastillaCoordinator = entry.runtime_data
    return coordinator


async def poll(
    coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory, minutes: int = 5
) -> None:
    freezer.tick(timedelta(minutes=minutes))
    await coordinator._async_refresh(scheduled=True)


def issue(
    hass: HomeAssistant, entry: MockConfigEntry, uuid: str = DEVICE_UUID
) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_device_detached_{uuid}")


def device_paths(backend: FakeBackend, uuid: str = DEVICE_UUID) -> list[str]:
    return [r.path for r in backend.requests if f"/device/{uuid}" in r.path]


# --------------------------------------------------------------- detaching


async def test_a_detached_device_raises_a_repair_issue_and_drops_out(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)

    found = issue(hass, entry)
    assert found is not None
    assert found.severity is ir.IssueSeverity.WARNING
    assert found.translation_key == "device_detached"
    assert found.translation_placeholders == {"name": "Synthetic softener"}
    assert found.is_fixable is False
    assert DEVICE_UUID not in coordinator.data.devices
    assert coordinator.data.detached == {DEVICE_UUID}
    assert coordinator.data.listed == {DEVICE_UUID}


async def test_its_entities_become_unavailable(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    entity = AquastillaEntity(coordinator, DEVICE_UUID)
    assert entity.available
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)
    assert not entity.available


async def test_polling_for_that_device_stops(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)  # detects it; the list is re-read once
    backend.requests.clear()
    for _ in range(5):
        await poll(coordinator, freezer)
    assert device_paths(backend) == []
    # The list is re-read once to see whether the device is gone, not at every poll.
    assert [r.path for r in backend.requests].count("/device/all") == 1


async def test_it_gets_one_more_chance_when_the_hourly_list_refresh_comes(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    backend.fail_always("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)
    backend.requests.clear()
    await poll(coordinator, freezer, minutes=int(DEVICE_LIST_INTERVAL.total_seconds() // 60))

    assert [r.path for r in backend.requests].count(STATE) == 1  # one retry
    assert issue(hass, entry) is not None  # still detached, issue stays
    assert coordinator.data.detached == {DEVICE_UUID}


async def test_the_issue_clears_when_the_device_is_attached_again(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)
    assert issue(hass, entry) is not None

    await poll(
        coordinator, freezer, minutes=int(DEVICE_LIST_INTERVAL.total_seconds() // 60)
    )  # the retry works

    assert issue(hass, entry) is None
    assert DEVICE_UUID in coordinator.data.devices
    assert coordinator.data.detached == frozenset()


# ----------------------------------------------------- removed from account


async def test_a_device_gone_from_the_account_loses_its_issue_and_its_registry_entry(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    backend.add_device(OTHER)  # a second device keeps the listing non-empty
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = coordinator_of(entry)
    registry = dr.async_get(hass)
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 2

    backend.remove_device(DEVICE_UUID)
    await poll(coordinator, freezer)  # the state read answers -3, the list is re-read
    await poll(coordinator, freezer)  # the list no longer has it
    await hass.async_block_till_done()

    assert issue(hass, entry) is None
    assert coordinator.data.listed == {OTHER}
    remaining = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert [next(iter(d.identifiers))[1] for d in remaining] == [OTHER]


async def test_an_empty_listing_does_not_wipe_the_registry(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One odd empty answer from the service must not delete every device and its entities."""
    coordinator = coordinator_of(entry)
    registry = dr.async_get(hass)
    backend.replace_json("/device/all", [])
    await poll(coordinator, freezer, minutes=int(DEVICE_LIST_INTERVAL.total_seconds() // 60))
    await hass.async_block_till_done()

    assert coordinator.data.listed == frozenset()
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1


async def test_a_push_refresh_keeps_the_listing_and_the_registry(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    registry = dr.async_get(hass)
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh_state(DEVICE_UUID, force=True)
    await coordinator.async_refresh_settings(DEVICE_UUID, force=True)
    await hass.async_block_till_done()

    assert coordinator.data.listed == {DEVICE_UUID}
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1


async def test_a_detached_but_still_listed_device_stays_in_the_registry(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    registry = dr.async_get(hass)
    backend.fail_always("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)
    await poll(coordinator, freezer)
    await hass.async_block_till_done()
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1


async def test_a_device_removed_from_the_account_is_cleaned_up_on_reload(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry
) -> None:
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "left-the-account")}, name="Old"
    )
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 2

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    remaining = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert [next(iter(d.identifiers))[1] for d in remaining] == [DEVICE_UUID]
    assert registry.async_get(stale.id) is None


# ----------------------------------------------------------- new devices


async def test_a_device_added_later_is_discovered_without_a_reload(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = coordinator_of(entry)
    backend.add_device(OTHER)
    await poll(coordinator, freezer, minutes=int(DEVICE_LIST_INTERVAL.total_seconds() // 60))
    await hass.async_block_till_done()

    assert set(coordinator.data.devices) == {DEVICE_UUID, OTHER}
    identifiers = {
        next(iter(d.identifiers))[1]
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    }
    assert identifiers == {DEVICE_UUID, OTHER}


async def test_one_detached_device_does_not_stop_the_other(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    backend.add_device(OTHER)
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = coordinator_of(entry)
    assert set(coordinator.data.devices) == {DEVICE_UUID, OTHER}

    backend.fail_always("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator, freezer)

    assert set(coordinator.data.devices) == {OTHER}
    assert issue(hass, entry, DEVICE_UUID) is not None
    assert issue(hass, entry, OTHER) is None


# ------------------------------------------------------------ never deletes


async def test_removing_the_entry_never_calls_the_vendors_delete_endpoint(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry
) -> None:
    backend.requests.clear()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert [r for r in backend.requests if r.method != "GET"] == []
    assert backend.forbidden == []
    assert not [r for r in backend.requests if "/delete" in r.path]


async def test_the_repair_issue_goes_when_the_entry_is_removed(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    backend.fail_always("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator_of(entry), freezer)
    assert issue(hass, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, entry) is None


async def test_a_reload_after_the_device_came_back_leaves_no_stale_issue(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator_of(entry), freezer)
    assert issue(hass, entry) is not None

    assert await hass.config_entries.async_reload(entry.entry_id)  # the device answers again now
    await hass.async_block_till_done()

    assert issue(hass, entry) is None
    assert DEVICE_UUID in coordinator_of(entry).data.devices


async def test_two_entries_do_not_share_repair_issues(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    other = MockConfigEntry(
        domain=DOMAIN, unique_id="someone-else", title="b@example.test", data=dict(entry.data)
    )
    await setup_entry(hass, other)
    backend.fail_next("GET", STATE, status=400, json_body=DETACHED)
    await poll(coordinator_of(entry), freezer)
    assert issue(hass, entry) is not None
    assert issue(hass, other) is None
