"""Sensors for EasyWallbox."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import EasyWallboxConfigEntry, WallboxState
from .entity import EasyWallboxEntity


@dataclass(frozen=True, kw_only=True)
class EasyWallboxSensorDescription(SensorEntityDescription):
    """Describes an EasyWallbox sensor."""

    value_fn: Callable[[WallboxState], float | str | None]
    # Defaults to "value is not None".
    available_fn: Callable[[WallboxState], bool] | None = None


def _app(fn: Callable[[protocol.AppData], float | str | None]):
    return lambda s: None if s.app_data is None else fn(s.app_data)


SENSORS: tuple[EasyWallboxSensorDescription, ...] = (
    EasyWallboxSensorDescription(
        key="charge_state",
        translation_key="charge_state",
        device_class=SensorDeviceClass.ENUM,
        options=protocol.CHARGE_STATE_OPTIONS,
        value_fn=_app(lambda a: a.charge_state),
    ),
    EasyWallboxSensorDescription(
        key="charging_power",
        translation_key="charging_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
        value_fn=_app(lambda a: a.charging_power),
    ),
    EasyWallboxSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_app(lambda a: a.session_energy),
    ),
    EasyWallboxSensorDescription(
        key="session_duration",
        translation_key="session_duration",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_app(lambda a: a.session_minutes),
    ),
    EasyWallboxSensorDescription(
        key="house_power",
        translation_key="house_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        value_fn=_app(lambda a: a.house_power),
    ),
    EasyWallboxSensorDescription(
        key="house_current",
        translation_key="house_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=_app(lambda a: a.house_current),
    ),
    EasyWallboxSensorDescription(
        key="charge_current_set",
        translation_key="charge_current_set",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda s: s.user_limit,
    ),
    EasyWallboxSensorDescription(
        key="dpm_limit_set",
        translation_key="dpm_limit_set",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda s: s.dpm_limit,
    ),
    EasyWallboxSensorDescription(
        key="pilot_voltage",
        translation_key="pilot_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_app(lambda a: a.pilot_voltage),
    ),
    EasyWallboxSensorDescription(
        key="offered_current",
        translation_key="offered_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_app(lambda a: a.offered_current),
        # None while there is no PWM signal: report "unknown", not unavailable.
        available_fn=lambda s: s.app_data is not None,
    ),
    EasyWallboxSensorDescription(
        key="safe_limit",
        translation_key="safe_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_app(lambda a: a.safe_limit),
    ),
    EasyWallboxSensorDescription(
        key="last_reply",
        translation_key="last_reply",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: None if s.last_line is None else s.last_line[:255],
        available_fn=lambda s: s.last_line is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyWallboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(EasyWallboxSensor(coordinator, d) for d in SENSORS)


class EasyWallboxSensor(EasyWallboxEntity, SensorEntity):
    """EasyWallbox sensor."""

    entity_description: EasyWallboxSensorDescription

    @property
    def native_value(self) -> float | str | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.state)

    @property
    def available(self) -> bool:
        """Unavailable when there is no fresh value."""
        if (fn := self.entity_description.available_fn) is not None:
            return super().available and fn(self.coordinator.state)
        return super().available and self.native_value is not None
