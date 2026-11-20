"""Charging start/stop switch for EasyWallbox."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EasyWallboxConfigEntry
from .entity import EasyWallboxEntity

CHARGING_SWITCH = SwitchEntityDescription(key="charging_enabled", translation_key="charging_enabled")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyWallboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch."""
    async_add_entities([EasyWallboxChargingSwitch(entry.runtime_data, CHARGING_SWITCH)])


class EasyWallboxChargingSwitch(EasyWallboxEntity, SwitchEntity):
    """Pause/resume charging by writing the DPM limit (0 A = paused).

    Same method as the upstream ESPHome firmware: native $CMD,CHARGE,STOP
    prevents resuming without re-plugging the cable on some units. Requires
    DPM to be installed and enabled.
    """

    @property
    def is_on(self) -> bool | None:
        """Return True if charging is allowed."""
        return self.coordinator.state.charge_enabled

    @property
    def available(self) -> bool:
        """Return availability."""
        return (
            super().available
            and self.coordinator.ble.authenticated
            and self.is_on is not None
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume charging."""
        await self.coordinator.async_resume_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause charging."""
        await self.coordinator.async_pause_charging()
