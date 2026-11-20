"""Binary sensors for EasyWallbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EasyWallboxConfigEntry, EasyWallboxCoordinator
from .entity import EasyWallboxEntity


@dataclass(frozen=True, kw_only=True)
class EasyWallboxBinaryDescription(BinarySensorEntityDescription):
    """Describes an EasyWallbox binary sensor."""

    value_fn: Callable[[EasyWallboxCoordinator], bool | None]
    always_available: bool = False


BINARY_SENSORS: tuple[EasyWallboxBinaryDescription, ...] = (
    EasyWallboxBinaryDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
        value_fn=lambda c: c.ble.authenticated,
    ),
    EasyWallboxBinaryDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda c: None if c.state.app_data is None else c.state.app_data.charging,
    ),
    EasyWallboxBinaryDescription(
        key="car_connected",
        translation_key="car_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda c: None
        if c.state.app_data is None
        else c.state.app_data.car_connected,
    ),
    EasyWallboxBinaryDescription(
        key="charge_enabled",
        translation_key="charge_enabled",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda c: c.state.charge_enabled,
    ),
    EasyWallboxBinaryDescription(
        key="dpm_enabled",
        translation_key="dpm_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: None
        if c.state.app_data is None
        else c.state.app_data.dpm_enabled,
    ),
    EasyWallboxBinaryDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: None if c.state.app_data is None else c.state.app_data.fault,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyWallboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(EasyWallboxBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class EasyWallboxBinarySensor(EasyWallboxEntity, BinarySensorEntity):
    """EasyWallbox binary sensor."""

    entity_description: EasyWallboxBinaryDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Return availability."""
        if self.entity_description.always_available:
            return True
        return super().available and self.is_on is not None
