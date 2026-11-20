"""Fixtures for EasyWallbox tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.easywallbox import protocol

pytest_plugins = ["pytest_homeassistant_custom_component"]

ADDRESS = "8C:F6:81:AD:B8:3E"
PIN = "9844"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
def skip_bluetooth_stack(hass):
    """Pretend the bluetooth stack is set up and the wallbox is in range."""
    hass.config.components.update({"bluetooth", "bluetooth_adapters"})
    device = MagicMock(address=ADDRESS, details={"source": "garage-bt-proxy"})
    with (
        patch(
            "custom_components.easywallbox.config_flow.async_discovered_service_info",
            return_value=[],
        ),
        patch(
            "custom_components.easywallbox.config_flow.async_ble_device_from_address",
            return_value=device,
        ),
        patch(
            "custom_components.easywallbox.coordinator.bluetooth.async_ble_device_from_address",
            return_value=device,
        ),
    ):
        yield


def ad_frame(state: int = 8) -> str:
    fields = ["0"] * 30
    fields[1], fields[3], fields[4], fields[6] = "89", "533", str(state), "1"
    fields[8], fields[9], fields[11], fields[19], fields[20] = "3680", "320", "125", "57", "42"
    return "$DATA,READ,AD," + ",".join(fields)


class FakeWallbox:
    """In-memory wallbox replacing EasyWallboxBle."""

    instances: list[FakeWallbox] = []

    def __init__(self, address, pin, getter, line_callback=None, connection_callback=None, **kw):
        self.address = address
        self.pin = pin
        self.line_callback = line_callback
        self.connection_callback = connection_callback
        self.authenticated = False
        self.eeprom = {protocol.IDX_USER_LIMIT: 160, protocol.IDX_DPM_LIMIT: 260}
        self.sent: list[str] = []
        self.reject_pin = False
        FakeWallbox.instances.append(self)

    def set_pin(self, pin):
        self.pin = pin

    async def connect(self):
        if self.reject_pin:
            from custom_components.easywallbox.wallbox import AuthenticationError

            raise AuthenticationError("bad pin")
        if not self.authenticated:
            self.authenticated = True
            if self.connection_callback:
                self.connection_callback(True)

    async def disconnect(self):
        if self.authenticated:
            self.authenticated = False
            if self.connection_callback:
                self.connection_callback(False)

    async def send(self, command, timeout=10, loose=False):
        await self.connect()
        self.sent.append(command)
        if command == protocol.CMD_READ_APP_DATA:
            reply = ad_frame()
        elif command.startswith("$EEP,READ,IDX,"):
            idx = int(command.split(",")[3])
            reply = f"{command},{self.eeprom.get(idx, 0)}"
        elif command.startswith("$EEP,WRITE,IDX,"):
            _, _, _, idx, raw = command.split(",")
            self.eeprom[int(idx)] = int(raw)
            reply = command
        else:
            reply = command + ",OK"
        if self.line_callback:
            self.line_callback(reply)
        return reply


@pytest.fixture
def fake_wallbox():
    """Patch the BLE transport with the fake wallbox."""
    FakeWallbox.instances.clear()
    with (
        patch("custom_components.easywallbox.coordinator.EasyWallboxBle", FakeWallbox),
        patch("custom_components.easywallbox.config_flow.EasyWallboxBle", FakeWallbox),
    ):
        yield FakeWallbox
