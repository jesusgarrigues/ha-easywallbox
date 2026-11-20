"""Base entity for EasyWallbox."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import EasyWallboxCoordinator


class EasyWallboxEntity(CoordinatorEntity[EasyWallboxCoordinator]):
    """Common EasyWallbox entity."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EasyWallboxCoordinator, description: EntityDescription
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        address = coordinator.address
        self._attr_unique_id = f"{address.lower()}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address.lower())},
            connections={(CONNECTION_BLUETOOTH, address.upper())},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
