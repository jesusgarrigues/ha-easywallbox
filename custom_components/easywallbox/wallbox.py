"""BLE transport for the EasyWallbox.

Works with any Home Assistant Bluetooth adapter, including ESPHome Bluetooth
proxies: Home Assistant routes the connection through whichever connectable
adapter/proxy has the best signal to the wallbox.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from . import protocol

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 10.0
BUSY_RETRY_DELAY = 1.5


class EasyWallboxError(Exception):
    """Base error."""


class DeviceNotFound(EasyWallboxError):
    """The wallbox is not reachable by any connectable adapter or proxy."""


class AuthenticationError(EasyWallboxError):
    """The wallbox rejected the PIN."""


class CommandTimeout(EasyWallboxError):
    """No matching reply was received in time."""


class CommandRejected(EasyWallboxError):
    """The wallbox answered with an error."""


class EasyWallboxBle:
    """Serialised command/response channel to an EasyWallbox over BLE."""

    def __init__(
        self,
        address: str,
        pin: str,
        ble_device_getter: Callable[[], BLEDevice | None],
        line_callback: Callable[[str], None] | None = None,
        connection_callback: Callable[[bool], None] | None = None,
        pair: bool = True,
        post_connect_delay: float = 3.0,
    ) -> None:
        """Initialise."""
        self.address = address
        self._pin = pin
        self._ble_device_getter = ble_device_getter
        self._line_callback = line_callback
        self._connection_callback = connection_callback
        self._pair = pair
        self._post_connect_delay = post_connect_delay

        self._client: BleakClientWithServiceCache | None = None
        self._authenticated = False
        self._lock = asyncio.Lock()
        self._rx_buffer = ""
        self._pending: tuple[str, bool, asyncio.Future[str]] | None = None
        self.last_line: str | None = None

    @property
    def authenticated(self) -> bool:
        """Return True when connected and logged in."""
        return (
            self._authenticated
            and self._client is not None
            and self._client.is_connected
        )

    def set_pin(self, pin: str) -> None:
        """Update the PIN (used after re-authentication)."""
        self._pin = pin

    async def send(
        self, command: str, timeout: float = COMMAND_TIMEOUT, loose: bool = False
    ) -> str:
        """Send a command and return the matching reply line."""
        async with self._lock:
            await self._ensure_connected()
            try:
                return await self._transact(command, timeout, loose)
            except CommandRejected as err:
                if str(err) != protocol.REPLY_BUSY:
                    raise
                await asyncio.sleep(BUSY_RETRY_DELAY)
                return await self._transact(command, timeout, loose)

    async def connect(self) -> None:
        """Connect and log in (no-op when already connected)."""
        async with self._lock:
            await self._ensure_connected()

    async def disconnect(self) -> None:
        """Disconnect from the wallbox."""
        async with self._lock:
            client = self._client
            self._client = None
            self._set_authenticated(False)
            if client is not None:
                try:
                    await client.disconnect()
                except BleakError as err:
                    _LOGGER.debug("Error while disconnecting: %s", err)

    # ------------------------------------------------------------------ internals

    async def _ensure_connected(self) -> None:
        if self.authenticated:
            return

        device = self._ble_device_getter()
        if device is None:
            raise DeviceNotFound(
                f"EasyWallbox {self.address} is not in range of any connectable "
                "Bluetooth adapter or ESPHome Bluetooth proxy"
            )

        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to %s via %s", self.address, device.details)
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    f"EasyWallbox {self.address}",
                    disconnected_callback=self._on_disconnected,
                    ble_device_callback=self._ble_device_getter,
                    max_attempts=3,
                )
            except (BleakError, TimeoutError) as err:
                raise EasyWallboxError(f"Unable to connect: {err}") from err
            self._client = client
            self._rx_buffer = ""

            if self._pair:
                # The wallbox requires an encrypted link (fixed passkey 1234).
                # On ESPHome proxies the passkey is answered by the proxy
                # firmware (see esphome/garage-bluetooth-proxy.yaml).
                try:
                    await client.pair()
                except NotImplementedError:
                    _LOGGER.debug("Adapter does not support explicit pairing")
                except (BleakError, TimeoutError) as err:
                    _LOGGER.debug("Pairing request failed (may already be bonded): %s", err)

            try:
                await client.start_notify(protocol.CHAR_NOTIFY_UUID, self._on_notify)
            except (BleakError, TimeoutError) as err:
                await self._drop_client()
                raise EasyWallboxError(
                    f"Unable to subscribe to notifications (pairing problem?): {err}"
                ) from err

            # Give the link time to settle encryption before logging in.
            await asyncio.sleep(self._post_connect_delay)

        try:
            await self._transact(protocol.cmd_auth(self._pin), COMMAND_TIMEOUT)
        except CommandRejected as err:
            await self._drop_client()
            raise AuthenticationError("Wallbox rejected the BLE PIN") from err
        except EasyWallboxError:
            # A silent login cannot recover: restart the BLE session next time.
            await self._drop_client()
            raise
        self._set_authenticated(True)
        _LOGGER.debug("Authenticated with %s", self.address)

    async def _drop_client(self) -> None:
        client = self._client
        self._client = None
        self._set_authenticated(False)
        if client is not None:
            try:
                await client.disconnect()
            except BleakError:
                pass

    async def _transact(self, command: str, timeout: float, loose: bool = False) -> str:
        client = self._client
        if client is None or not client.is_connected:
            raise EasyWallboxError("Not connected")

        command = command.strip()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending = (command, loose, future)
        payload = (command + "\n").encode()
        # The wallbox is a serial bridge waiting for "\n", so chunking is safe
        # and avoids depending on the negotiated MTU.
        chunk = max(20, (client.mtu_size or 23) - 3)
        try:
            for start in range(0, len(payload), chunk):
                await client.write_gatt_char(
                    protocol.CHAR_WRITE_UUID,
                    payload[start : start + chunk],
                    response=True,
                )
            _LOGGER.debug("TX %s", command if "AUTH" not in command else "$BLE,AUTH,****")
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise CommandTimeout(f"No reply to {command}") from err
        except BleakError as err:
            await self._drop_client()
            raise EasyWallboxError(f"BLE write failed: {err}") from err
        finally:
            self._pending = None

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        self._rx_buffer += bytes(data).decode("utf-8", errors="ignore")
        if len(self._rx_buffer) > 4096:
            _LOGGER.warning("RX buffer overflow, discarding")
            self._rx_buffer = ""
            return
        while "\n" in self._rx_buffer:
            line, self._rx_buffer = self._rx_buffer.split("\n", 1)
            line = line.strip("\r ")
            if line:
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        _LOGGER.debug("RX %s", line)
        self.last_line = line
        if self._pending is not None:
            command, loose, future = self._pending
            if not future.done():
                if protocol.reply_matches(command, line, loose):
                    future.set_result(line)
                elif protocol.is_error_reply(line):
                    future.set_exception(CommandRejected(line))
        if self._line_callback is not None:
            self._line_callback(line)

    def _on_disconnected(self, client: object) -> None:
        if self._client is not None and client is not self._client:
            return  # Stale callback from a previous connection.
        _LOGGER.debug("Disconnected from %s", self.address)
        self._client = None
        self._set_authenticated(False)
        if self._pending is not None and not self._pending[2].done():
            self._pending[2].set_exception(EasyWallboxError("Disconnected"))

    def _set_authenticated(self, value: bool) -> None:
        changed = self._authenticated != value
        self._authenticated = value
        if changed and self._connection_callback is not None:
            self._connection_callback(value)
