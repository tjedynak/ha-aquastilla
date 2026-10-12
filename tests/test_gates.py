"""When a command is refused, from the data the coordinator already holds."""

from dataclasses import replace

import pytest

from custom_components.aquastilla import models
from custom_components.aquastilla.coordinator import DeviceData
from custom_components.aquastilla.gates import Command, Reason, blocked_reason, is_regenerating

from .common import load_fixture


def data(*, cycle: str | None = None, **settings: object) -> DeviceData:
    state = {**load_fixture("state")}
    current = load_fixture("current_states")
    if cycle is not None:
        state["state"] = cycle
        current = {"first": cycle, "second": cycle}
    return DeviceData(
        device=models.Device.from_json(load_fixture("device")),
        state=models.DeviceState.from_json(state),
        settings=models.DeviceSettings.from_json({**load_fixture("settings"), **settings}),
        current_states=models.CurrentStates.from_json(current),
    )


OWNER_COMMANDS = [
    Command.ADVANCE_CYCLE,
    Command.VACATION_MODE,
    Command.VACATION_MODE_ON,
    Command.FORCE_REGENERATION,
    Command.DELAY_REGENERATION,
    Command.CLOSE_WATER_FLOW,
    Command.SETTING,
]


def test_an_idle_device_takes_every_command() -> None:
    idle = data(serviceMode=False, waterFlow=True, vacationMode=False)
    assert all(blocked_reason(idle, command) is None for command in [*Command])


@pytest.mark.parametrize("command", OWNER_COMMANDS)
def test_service_mode_blocks_the_owner_controls(command: Command) -> None:
    assert blocked_reason(data(serviceMode=True), command) is Reason.SERVICE_MODE


def test_service_mode_does_not_block_the_service_actions() -> None:
    assert blocked_reason(data(serviceMode=True), Command.SERVICE) is None


@pytest.mark.parametrize(
    "command",
    [
        Command.ADVANCE_CYCLE,
        Command.VACATION_MODE,
        Command.VACATION_MODE_ON,
        Command.FORCE_REGENERATION,
        Command.DELAY_REGENERATION,
        Command.CLOSE_WATER_FLOW,
    ],
)
def test_a_closed_water_flow_blocks_what_needs_the_flow(command: Command) -> None:
    assert blocked_reason(data(waterFlow=False), command) is Reason.WATER_FLOW_CLOSED


def test_settings_can_still_be_changed_with_the_flow_closed() -> None:
    assert blocked_reason(data(waterFlow=False), Command.SETTING) is None


@pytest.mark.parametrize("command", [Command.FORCE_REGENERATION, Command.DELAY_REGENERATION])
def test_a_running_regeneration_blocks_force_and_postpone(command: Command) -> None:
    assert blocked_reason(data(cycle="deviceStateRegenBackwash"), command) is Reason.REGENERATING


def test_vacation_mode_blocks_forcing_and_postponing_but_not_the_toggle() -> None:
    vacation = data(vacationMode=True)
    assert blocked_reason(vacation, Command.FORCE_REGENERATION) is Reason.VACATION_MODE
    assert blocked_reason(vacation, Command.DELAY_REGENERATION) is Reason.VACATION_MODE
    assert blocked_reason(vacation, Command.VACATION_MODE) is None
    assert blocked_reason(vacation, Command.VACATION_MODE_ON) is None


def test_vacation_mode_cannot_be_turned_on_during_a_regeneration_but_can_be_turned_off() -> None:
    running = data(cycle="deviceStateRegenBackwash")
    assert blocked_reason(running, Command.VACATION_MODE_ON) is Reason.REGENERATING
    assert blocked_reason(running, Command.VACATION_MODE) is None


def test_unknown_values_never_block() -> None:
    unknown = data()
    unknown = replace(
        unknown,
        settings=replace(unknown.settings, service_mode=None, water_flow=None, vacation_mode=None),
        state=replace(unknown.state, cycle=None),
        current_states=models.CurrentStates.from_json({}),
    )
    assert all(blocked_reason(unknown, command) is None for command in [*Command])
    assert is_regenerating(unknown) is None


def test_an_unknown_cycle_is_neither_yes_nor_no() -> None:
    assert is_regenerating(data(cycle="deviceStateSomethingNew")) is None


def test_an_offline_device_refuses_what_is_physical_and_cannot_be_taken_back() -> None:
    offline = data()
    offline = replace(offline, state=replace(offline.state, is_online=False))
    refused = {c for c in Command if blocked_reason(offline, c) is Reason.OFFLINE}
    assert refused == {
        Command.ADVANCE_CYCLE,
        Command.CLOSE_WATER_FLOW,
        Command.FORCE_REGENERATION,
    }
    for harmless in (Command.VACATION_MODE, Command.DELAY_REGENERATION, Command.SETTING):
        assert blocked_reason(offline, harmless) is None
