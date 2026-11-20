"""Data update coordinator for EasyWallbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import protocol
from .const import (
    CONF_DPM_DEFAULT,
    CONF_PAIR,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_DPM_DEFAULT,
    DEFAULT_PAIR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_CURRENT,
)
from .wallbox import (
    AuthenticationError,
    CommandTimeout,
    EasyWallboxBle,
    EasyWallboxError,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type EasyWallboxConfigEntry = ConfigEntry[EasyWallboxCoordinator]


@dataclass(slots=True)
class WallboxState:
    """Latest known wallbox state."""

    app_data: protocol.AppData | None = None
    user_limit_raw: int | None = None
    dpm_limit_raw: int | None = None
    last_line: str | None = None

    @property
    def user_limit(self) -> float | None:
        """Confirmed charging current limit (A)."""
        return None if self.user_limit_raw is None else protocol.raw_to_amps(self.user_limit_raw)

    @property
    def dpm_limit(self) -> float | None:
        """Confirmed DPM (house + EV) current limit (A)."""
        return None if self.dpm_limit_raw is None else protocol.raw_to_amps(self.dpm_limit_raw)

    @property
    def charge_enabled(self) -> bool | None:
        """True if the confirmed DPM limit is above zero (not paused)."""
        return None if self.dpm_limit_raw is None else self.dpm_limit_raw > 0


class EasyWallboxCoordinator(DataUpdateCoordinator[WallboxState]):
    """Polls the wallbox and executes commands over BLE."""

    config_entry: EasyWallboxConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EasyWallboxConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.state = WallboxState()
        self.ble = EasyWallboxBle(
            self.address,
            entry.data[CONF_PIN],
            self._get_ble_device,
            line_callback=self._handle_line,
            connection_callback=self._handle_connection,
            pair=entry.options.get(CONF_PAIR, DEFAULT_PAIR),
        )
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # Last non-zero DPM limit (A); restored when charging is resumed.
        self.dpm_resume_limit: float | None = None

    @property
    def dpm_default(self) -> float:
        """Fallback DPM limit when none is remembered."""
        return float(self.config_entry.options.get(CONF_DPM_DEFAULT, DEFAULT_DPM_DEFAULT))

    async def async_load(self) -> None:
        """Load persisted data."""
        if (stored := await self._store.async_load()) is not None:
            self.dpm_resume_limit = stored.get("dpm_resume_limit")

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"dpm_resume_limit": self.dpm_resume_limit}, 5
        )

    @callback
    def _get_ble_device(self):
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    # ------------------------------------------------------------- BLE callbacks

    @callback
    def _handle_connection(self, connected: bool) -> None:
        if not connected:
            # Never report old measurements as live after losing the link.
            self.state.app_data = None
        self.async_update_listeners()

    @callback
    def _handle_line(self, line: str) -> None:
        self.state.last_line = line
        if (app := protocol.parse_app_data(line)) is not None:
            self.state.app_data = app
            return
        if (idx := protocol.parse_eep_idx(line)) is not None:
            index, raw = idx
            if index == protocol.IDX_USER_LIMIT:
                self.state.user_limit_raw = raw
            elif index == protocol.IDX_DPM_LIMIT:
                self.state.dpm_limit_raw = raw
                if raw > 0:
                    amps = protocol.raw_to_amps(raw)
                    if amps != self.dpm_resume_limit:
                        self.dpm_resume_limit = amps
                        self._save()

    # ------------------------------------------------------------------ polling

    async def _async_update_data(self) -> WallboxState:
        try:
            await self.ble.send(protocol.CMD_READ_APP_DATA)
            await self.ble.send(protocol.cmd_read_idx(protocol.IDX_USER_LIMIT))
            await self.ble.send(protocol.cmd_read_idx(protocol.IDX_DPM_LIMIT))
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EasyWallboxError as err:
            raise UpdateFailed(str(err)) from err
        return self.state

    async def async_shutdown(self) -> None:
        """Disconnect on unload."""
        await super().async_shutdown()
        await self._store.async_save({"dpm_resume_limit": self.dpm_resume_limit})
        await self.ble.disconnect()

    # ----------------------------------------------------------------- commands

    async def _write_idx(self, index: int, raw: int) -> None:
        """Write an EEPROM index; on a missing echo read it back, never re-write."""
        command = protocol.cmd_write_idx(index, raw)
        try:
            try:
                await self.ble.send(command)
            except CommandTimeout:
                _LOGGER.debug("No echo for %s, reading register back", command)
                reply = await self.ble.send(protocol.cmd_read_idx(index))
                parsed = protocol.parse_eep_idx(reply)
                if parsed is None or parsed[1] != raw:
                    raise HomeAssistantError(
                        f"EasyWallbox did not apply {command} (read back: {reply})"
                    ) from None
        except EasyWallboxError as err:
            raise HomeAssistantError(f"EasyWallbox command failed: {err}") from err
        finally:
            self.async_update_listeners()

    async def async_set_charge_current(self, amps: float) -> None:
        """Set the vehicle charging current limit (A)."""
        await self._write_idx(protocol.IDX_USER_LIMIT, protocol.amps_to_raw(amps))

    async def async_set_dpm_limit(self, amps: float) -> None:
        """Set the house + EV DPM limit (A).

        While charging is paused (DPM = 0) the value is only remembered and
        applied on the next resume, exactly like the upstream firmware.
        """
        self.dpm_resume_limit = amps
        self._save()
        if self.state.charge_enabled:
            await self._write_idx(protocol.IDX_DPM_LIMIT, protocol.amps_to_raw(amps))
        else:
            self.async_update_listeners()

    async def async_resume_charging(self) -> None:
        """Resume charging by restoring the last valid DPM limit."""
        amps = self.dpm_resume_limit
        if amps is None or amps < MIN_CURRENT:
            amps = self.dpm_default
        await self._write_idx(protocol.IDX_DPM_LIMIT, protocol.amps_to_raw(amps))

    async def async_pause_charging(self) -> None:
        """Pause charging by setting the DPM limit to 0 A (requires DPM)."""
        await self._write_idx(protocol.IDX_DPM_LIMIT, 0)

    async def async_send_raw(self, command: str, timeout: float) -> str:
        """Send an arbitrary protocol command and return the reply."""
        try:
            reply = await self.ble.send(command, timeout=timeout, loose=True)
        except EasyWallboxError as err:
            raise HomeAssistantError(f"EasyWallbox command failed: {err}") from err
        self.async_update_listeners()
        return reply

    async def async_reconnect(self) -> None:
        """Force a fresh BLE session."""
        await self.ble.disconnect()
        await self.async_refresh()
