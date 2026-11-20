"""Config flow for EasyWallbox."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.device_registry import format_mac

from . import protocol
from .const import (
    CONF_DPM_DEFAULT,
    CONF_MAX_CURRENT,
    CONF_PAIR,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_DPM_DEFAULT,
    DEFAULT_MAX_CURRENT,
    DEFAULT_NAME,
    DEFAULT_PAIR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_CURRENT,
)
from .wallbox import AuthenticationError, EasyWallboxBle, EasyWallboxError

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "manual"
PIN_RE = re.compile(r"^\d{4,6}$")
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _is_wallbox(info: BluetoothServiceInfoBleak) -> bool:
    name = (info.name or "").lower()
    return (
        protocol.SERVICE_UUID in [u.lower() for u in info.service_uuids]
        or name.startswith("ewb")
        or "wallbox" in name
    )


class EasyWallboxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EasyWallbox."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._address: str | None = None
        self._name: str = DEFAULT_NAME
        self._discovered: dict[str, tuple[str, int]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> EasyWallboxOptionsFlow:
        """Return the options flow."""
        return EasyWallboxOptionsFlow()

    async def _async_validate(self, address: str, pin: str) -> str | None:
        """Try to connect and log in. Return an error key or None."""
        if async_ble_device_from_address(self.hass, address, connectable=True) is None:
            return "not_in_range"
        ble = EasyWallboxBle(
            address,
            pin,
            lambda: async_ble_device_from_address(self.hass, address, connectable=True),
        )
        try:
            await ble.connect()
        except AuthenticationError:
            return "invalid_auth"
        except EasyWallboxError as err:
            _LOGGER.warning("Could not connect to EasyWallbox %s: %s", address, err)
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating EasyWallbox")
            return "unknown"
        finally:
            await ble.disconnect()
        return None

    # ---------------------------------------------------------------- discovery

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a wallbox discovered over Bluetooth (local adapter or proxy)."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_pin()

    # --------------------------------------------------------------------- user

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a discovered wallbox or choose manual entry."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL_ENTRY:
                return await self.async_step_manual()
            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = self._discovered[address][0]
            return await self.async_step_pin()

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if format_mac(info.address) in current or not _is_wallbox(info):
                continue
            self._discovered[info.address] = (info.name or DEFAULT_NAME, info.rssi)

        if not self._discovered:
            return await self.async_step_manual()

        options = [
            selector.SelectOptionDict(
                value=addr, label=f"{name} ({addr}, {rssi} dBm)"
            )
            for addr, (name, rssi) in self._discovered.items()
        ]
        options.append(selector.SelectOptionDict(value=MANUAL_ENTRY, label="MAC manual / manual MAC"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the MAC address manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if MAC_RE.match(address) is None:
                errors["base"] = "invalid_mac"
            else:
                await self.async_set_unique_id(format_mac(address))
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = f"{DEFAULT_NAME} {address[-5:]}"
                return await self.async_step_pin()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the 4 digit BLE PIN (field A: of the QR label) and validate it."""
        assert self._address is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = user_input[CONF_PIN].strip()
            if PIN_RE.match(pin) is None:
                errors["base"] = "invalid_pin"
            elif (error := await self._async_validate(self._address, pin)) is None:
                return self.async_create_entry(
                    title=self._name,
                    data={CONF_ADDRESS: self._address, CONF_PIN: pin},
                )
            else:
                errors["base"] = error

        return self.async_show_form(
            step_id="pin",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            description_placeholders={"name": self._name, "address": self._address},
            errors=errors,
        )

    # ------------------------------------------------------------------- reauth

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected PIN."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new PIN."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None and PIN_RE.match(user_input[CONF_PIN].strip()) is None:
            errors["base"] = "invalid_pin"
        elif user_input is not None:
            # Release the running connection so the wallbox accepts a new one.
            if (coordinator := getattr(entry, "runtime_data", None)) is not None:
                await coordinator.ble.disconnect()
            error = await self._async_validate(entry.data[CONF_ADDRESS], user_input[CONF_PIN])
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PIN: user_input[CONF_PIN]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            errors=errors,
        )


def _number(minimum: float, maximum: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


class EasyWallboxOptionsFlow(OptionsFlow):
    """Options for EasyWallbox."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): _number(10, 600, 5, "s"),
                    vol.Required(
                        CONF_MAX_CURRENT,
                        default=opts.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT),
                    ): _number(MIN_CURRENT, 80, 0.5, "A"),
                    vol.Required(
                        CONF_DPM_DEFAULT,
                        default=opts.get(CONF_DPM_DEFAULT, DEFAULT_DPM_DEFAULT),
                    ): _number(MIN_CURRENT, 100, 0.5, "A"),
                    vol.Required(
                        CONF_PAIR, default=opts.get(CONF_PAIR, DEFAULT_PAIR)
                    ): bool,
                }
            ),
        )
