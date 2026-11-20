"""Number entities (current limits) for EasyWallbox."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT, MIN_CURRENT
from .coordinator import EasyWallboxConfigEntry, EasyWallboxCoordinator
from .entity import EasyWallboxEntity

CHARGE_CURRENT = NumberEntityDescription(
    key="charge_current",
    translation_key="charge_current",
    device_class=NumberDeviceClass.CURRENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    native_min_value=MIN_CURRENT,
    native_step=0.5,
    mode=NumberMode.SLIDER,
)

DPM_LIMIT = NumberEntityDescription(
    key="dpm_limit",
    translation_key="dpm_limit",
    device_class=NumberDeviceClass.CURRENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    native_min_value=MIN_CURRENT,
    native_step=0.5,
    mode=NumberMode.BOX,
    # Changing the household limit is risky: hidden by default like upstream.
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyWallboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [ChargeCurrentNumber(coordinator, CHARGE_CURRENT), DpmLimitNumber(coordinator, DPM_LIMIT)]
    )


class _BaseNumber(EasyWallboxEntity, NumberEntity):
    def __init__(self, coordinator: EasyWallboxCoordinator, description) -> None:
        super().__init__(coordinator, description)
        self._attr_native_max_value = float(
            coordinator.config_entry.options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
        )


class ChargeCurrentNumber(_BaseNumber):
    """Vehicle charging current limit (EEPROM index 174)."""

    @property
    def native_value(self) -> float | None:
        """Return the confirmed value."""
        return self.coordinator.state.user_limit

    @property
    def available(self) -> bool:
        """Return availability."""
        return super().available and self.coordinator.ble.authenticated

    async def async_set_native_value(self, value: float) -> None:
        """Set the limit."""
        await self.coordinator.async_set_charge_current(value)


class DpmLimitNumber(_BaseNumber):
    """House + EV DPM limit (EEPROM index 158).

    Shows the limit that will be applied while charging (remembered even when
    charging is paused with DPM = 0).
    """

    @property
    def native_value(self) -> float | None:
        """Return the active/remembered limit."""
        state = self.coordinator.state
        if state.charge_enabled:
            return state.dpm_limit
        return self.coordinator.dpm_resume_limit or self.coordinator.dpm_default

    @property
    def available(self) -> bool:
        """Return availability."""
        return super().available and self.coordinator.ble.authenticated

    async def async_set_native_value(self, value: float) -> None:
        """Set the limit."""
        await self.coordinator.async_set_dpm_limit(value)
