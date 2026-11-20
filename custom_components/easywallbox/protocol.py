"""EasyWallbox BLE protocol helpers.

Pure Python (no Home Assistant imports) so it can be unit tested in isolation.

The protocol is a line based ASCII protocol tunnelled over a BLE serial bridge
(Silicon Labs BGX13P).  It was reverse engineered by the community, see
https://github.com/paolofaz/Easywallbox---Esphome and
https://github.com/mihaistana/easywallbox.
"""

from __future__ import annotations

from dataclasses import dataclass

SERVICE_UUID = "331a36f5-2459-45ea-9d95-6142f0c4b307"
# Characteristic we write commands to (wallbox RX).
CHAR_WRITE_UUID = "a9da6040-0823-4995-94ec-9ce41ca28833"
# Characteristic the wallbox notifies replies on (wallbox TX).
CHAR_NOTIFY_UUID = "a73e9a10-628f-4494-a099-12efaf72258f"

# EEPROM indexes. Values are expressed in tenths of amperes (A x 10).
IDX_SAFE_LIMIT = 156
IDX_DPM_LIMIT = 158
IDX_USER_LIMIT = 174
IDX_DPM_ENABLED = 178

CMD_READ_APP_DATA = "$DATA,READ,AD"

REPLY_AUTH_OK = "$BLE,AUTH,OK"
REPLY_AUTH_FAIL = "$BLE,AUTH,FAIL"
REPLY_WRITE_FAIL = "$EEP,WRITE,FAIL"
REPLY_BUSY = "$ERR,BUSY"

# Nominal single phase voltage used to convert amperes to kW, like upstream.
NOMINAL_VOLTAGE = 230.0

CHARGE_STATE_UNPLUGGED = "unplugged"
CHARGE_STATE_PAUSE = "pause"
CHARGE_STATE_VEHICLE_READY = "vehicle_ready"
CHARGE_STATE_CHARGING = "charging"
CHARGE_STATE_ERROR = "error"
CHARGE_STATE_UNKNOWN = "unknown"

CHARGE_STATES: dict[int, str] = {
    3: CHARGE_STATE_UNPLUGGED,
    5: CHARGE_STATE_PAUSE,
    6: CHARGE_STATE_VEHICLE_READY,
    8: CHARGE_STATE_CHARGING,
    12: CHARGE_STATE_ERROR,
}

CHARGE_STATE_OPTIONS = [
    CHARGE_STATE_UNPLUGGED,
    CHARGE_STATE_PAUSE,
    CHARGE_STATE_VEHICLE_READY,
    CHARGE_STATE_CHARGING,
    CHARGE_STATE_ERROR,
    CHARGE_STATE_UNKNOWN,
]


def cmd_auth(pin: str) -> str:
    """Return the login command."""
    return f"$BLE,AUTH,{pin}"


def cmd_read_idx(idx: int) -> str:
    """Return an EEPROM index read command."""
    return f"$EEP,READ,IDX,{idx}"


def cmd_write_idx(idx: int, raw: int) -> str:
    """Return an EEPROM index write command."""
    return f"$EEP,WRITE,IDX,{idx},{int(raw)}"


def amps_to_raw(amps: float) -> int:
    """Convert amperes to the wallbox raw unit (A x 10)."""
    return int(round(amps * 10))


def raw_to_amps(raw: int) -> float:
    """Convert the wallbox raw unit (A x 10) to amperes."""
    return raw / 10.0


def amps_to_kw(amps: float) -> float:
    """Convert amperes to kW using the nominal single phase voltage."""
    return round(amps * NOMINAL_VOLTAGE / 1000.0, 2)


def _tokens(line: str) -> list[str]:
    # Upstream firmware tokenises with strtok(), which skips empty fields.
    # Mimic it so that field indexes match exactly.
    return [tok for tok in line.split(",") if tok]


def is_error_reply(line: str) -> bool:
    """Return True if the line is an error reply."""
    return line.startswith("$ERR,") or line in (REPLY_AUTH_FAIL, REPLY_WRITE_FAIL)


def reply_matches(command: str, line: str, loose: bool = False) -> bool:
    """Return True if ``line`` is the reply to ``command``.

    ``loose`` matching is used for arbitrary user commands, where the exact
    reply format is unknown: any line sharing the first two tokens matches.
    """
    command = command.strip()
    if command.startswith("$BLE,AUTH,"):
        return line == REPLY_AUTH_OK
    if command == "$BLE,LOGOUT":
        return line == "$BLE,LOGOUT,OK"
    if command.startswith("$EEP,WRITE,IDX,"):
        # The wallbox echoes successful writes verbatim.
        return line == command
    if line == command or line.startswith(command + ","):
        return True
    if loose:
        prefix = ",".join(command.split(",")[:2])
        return line.startswith(prefix)
    return False


def parse_eep_idx(line: str) -> tuple[int, int] | None:
    """Parse ``$EEP,READ,IDX,<idx>,<value>`` (or the WRITE echo)."""
    if not (line.startswith("$EEP,READ,IDX,") or line.startswith("$EEP,WRITE,IDX,")):
        return None
    parts = _tokens(line)
    if len(parts) < 5:
        return None
    try:
        return int(parts[3]), int(float(parts[4]))
    except ValueError:
        return None


@dataclass(slots=True)
class AppData:
    """Telemetry contained in a ``$DATA,READ,AD`` frame."""

    state_code: int
    charge_state: str
    pilot_voltage: float
    pwm_raw: int
    offered_current: float | None
    dpm_enabled: bool
    charging_power: float
    safe_limit: float
    house_current: float
    fault: bool
    session_energy: float
    session_minutes: int

    @property
    def charging(self) -> bool:
        """Return True if the charger reports it is charging."""
        return self.state_code == 8

    @property
    def car_connected(self) -> bool:
        """Return True if a vehicle is plugged in."""
        return self.state_code in (5, 6, 8)

    @property
    def house_power(self) -> float:
        """Return the total house power measured by the DPM clamp (kW)."""
        return amps_to_kw(self.house_current)


def parse_app_data(line: str) -> AppData | None:
    """Parse a ``$DATA,READ,AD`` telemetry frame.

    Field N of the protocol is ``parts[N + 3]`` (``parts[0..2]`` is the
    ``$DATA,READ,AD`` header). Field meanings follow the upstream ESPHome
    firmware by paolofaz.
    """
    if not line.startswith(CMD_READ_APP_DATA + ","):
        return None
    parts = _tokens(line)
    if len(parts) < 25:
        return None
    try:
        state_code = int(parts[7])
        pwm_raw = int(parts[6])
        # 1000 means "no PWM"; IEC 61851 pilot current = duty(0.1 %) * 0.06 A.
        offered = round(pwm_raw * 0.06, 1) if 100 <= pwm_raw <= 850 else None
        return AppData(
            state_code=state_code,
            charge_state=CHARGE_STATES.get(state_code, CHARGE_STATE_UNKNOWN),
            pilot_voltage=float(parts[4]) / 10.0,
            pwm_raw=pwm_raw,
            offered_current=offered,
            dpm_enabled=int(parts[9]) == 1,
            charging_power=float(parts[11]) / 1000.0,
            safe_limit=float(parts[12]) / 10.0,
            house_current=float(parts[14]) / 10.0,
            fault=int(parts[21]) != 0,
            session_energy=float(parts[22]) / 10.0,
            session_minutes=int(parts[23]),
        )
    except (ValueError, IndexError):
        return None
