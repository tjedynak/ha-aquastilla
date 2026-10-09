"""Guard: the test suite must never open a network socket.

The vendor backend is third-party production infrastructure and CI must never
reach it. pytest-homeassistant-custom-component blocks sockets by default; this
test fails loudly if that protection is ever switched off.
"""

import socket

import pytest
from pytest_homeassistant_custom_component.plugins import HASocketBlockedError


@pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")
def test_sockets_are_blocked_in_tests() -> None:
    with pytest.raises(HASocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # The plugin fails any test that triggered a blocked socket; this one did so
    # on purpose, so clear the record.
    HASocketBlockedError.instances.clear()
