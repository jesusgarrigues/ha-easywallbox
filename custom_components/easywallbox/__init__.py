"""The EasyWallbox (BLE) integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_COMMAND,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_TIMEOUT,
    DOMAIN,
    SERVICE_SEND_COMMAND,
)
from .coordinator import EasyWallboxConfigEntry, EasyWallboxCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_COMMAND): vol.All(cv.string, vol.Match(r"^\$[A-Z]+,")),
        vol.Optional(ATTR_TIMEOUT, default=10): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=60)
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration wide services."""

    async def _send_command(call: ServiceCall) -> ServiceResponse:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            raise ServiceValidationError("Unknown or not loaded EasyWallbox entry")
        coordinator: EasyWallboxCoordinator = entry.runtime_data
        reply = await coordinator.async_send_raw(
            call.data[ATTR_COMMAND], call.data[ATTR_TIMEOUT]
        )
        return {"reply": reply}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _send_command,
        schema=SEND_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EasyWallboxConfigEntry) -> bool:
    """Set up EasyWallbox from a config entry."""
    coordinator = EasyWallboxCoordinator(hass, entry)
    await coordinator.async_load()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Free the proxy connection slot before Home Assistant retries.
        await coordinator.ble.disconnect()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: EasyWallboxConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EasyWallboxConfigEntry) -> bool:
    """Unload a config entry."""
    # The coordinator shutdown (BLE disconnect) runs automatically on unload.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
