"""Buttons for EasyWallbox."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EasyWallboxConfigEntry
from .entity import EasyWallboxEntity

REFRESH = ButtonEntityDescription(key="refresh", translation_key="refresh")
RECONNECT = ButtonEntityDescription(
    key="reconnect", translation_key="reconnect", entity_category=EntityCategory.DIAGNOSTIC
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyWallboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [RefreshButton(coordinator, REFRESH), ReconnectButton(coordinator, RECONNECT)]
    )


class RefreshButton(EasyWallboxEntity, ButtonEntity):
    """Request fresh telemetry and settings."""

    @property
    def available(self) -> bool:
        """Always pressable."""
        return True

    async def async_press(self) -> None:
        """Refresh now."""
        await self.coordinator.async_refresh()


class ReconnectButton(EasyWallboxEntity, ButtonEntity):
    """Drop and re-establish the BLE session."""

    @property
    def available(self) -> bool:
        """Always pressable."""
        return True

    async def async_press(self) -> None:
        """Reconnect."""
        await self.coordinator.async_reconnect()
