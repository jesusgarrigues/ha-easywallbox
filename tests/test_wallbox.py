"""Tests for the BLE transport using a fake bleak client."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from custom_components.easywallbox import wallbox as wb


class FakeBleakClient:
    """Emulates the BGX13P serial bridge of the wallbox."""

    def __init__(self, pin="9844"):
        self.pin = pin
        self.is_connected = True
        self.mtu_size = 23
        self.writes: list[bytes] = []
        self._buf = b""
        self._notify = None
        self.paired = False

    async def pair(self):
        self.paired = True

    async def start_notify(self, uuid, cb):
        self._notify = cb

    async def disconnect(self):
        self.is_connected = False

    async def write_gatt_char(self, uuid, data, response=True):
        assert len(data) <= 20
        self.writes.append(bytes(data))
        self._buf += bytes(data)
        if b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            asyncio.get_running_loop().call_soon(self._answer, line.decode())

    def _answer(self, cmd: str):
        if cmd.startswith("$BLE,AUTH,"):
            reply = "$BLE,AUTH,OK" if cmd.endswith(self.pin) else "$BLE,AUTH,FAIL"
        elif cmd.startswith("$EEP,WRITE"):
            reply = cmd
        elif cmd == "$EEP,READ,IDX,174":
            reply = "$EEP,READ,IDX,174,160"
        else:
            return  # silence -> timeout
        data = (reply + "\r\n").encode()
        # Deliver in two fragments to exercise line reassembly.
        self._notify(None, bytearray(data[:7]))
        self._notify(None, bytearray(data[7:]))


async def _make(pin_ok=True):
    client = FakeBleakClient(pin="9844" if pin_ok else "0000")

    async def fake_establish(*args, **kwargs):
        return client

    lines: list[str] = []
    ble = wb.EasyWallboxBle(
        "8C:F6:81:AD:B8:3E",
        "9844",
        lambda: MagicMock(details={}),
        line_callback=lines.append,
        post_connect_delay=0,
    )
    return ble, client, lines, patch.object(wb, "establish_connection", fake_establish)


async def test_auth_read_write() -> None:
    ble, client, lines, patcher = await _make()
    with patcher:
        reply = await ble.send("$EEP,READ,IDX,174")
        assert reply == "$EEP,READ,IDX,174,160"
        assert ble.authenticated and client.paired
        reply = await ble.send("$EEP,WRITE,IDX,158,260")
        assert reply == "$EEP,WRITE,IDX,158,260"
    # Long command was chunked into <= 20 byte writes and reassembled.
    assert b"".join(client.writes).count(b"\n") == 3
    assert lines[0] == "$BLE,AUTH,OK"


async def test_bad_pin() -> None:
    ble, client, _, patcher = await _make(pin_ok=False)
    with patcher, pytest.raises(wb.AuthenticationError):
        await ble.connect()
    assert not ble.authenticated
    assert not client.is_connected


async def test_timeout() -> None:
    ble, _, _, patcher = await _make()
    with patcher, pytest.raises(wb.CommandTimeout):
        await ble.send("$DATA,READ,SV", timeout=0.05)
    # Link still usable after a timeout.
    assert ble.authenticated
