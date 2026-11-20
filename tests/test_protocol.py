"""Tests for the pure protocol helpers (no Home Assistant needed)."""

import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "ewb_protocol",
    Path(__file__).parents[1] / "custom_components" / "easywallbox" / "protocol.py",
)
protocol = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = protocol
_spec.loader.exec_module(protocol)


def _ad_frame(**overrides):
    # 30 numeric fields after the header; field N is index N.
    fields = ["0"] * 30
    fields[1] = "89"     # pilot voltage x10 -> 8.9 V
    fields[3] = "533"    # PWM 53.3 % -> 32 A offered
    fields[4] = "8"      # state: charging
    fields[6] = "1"      # DPM enabled
    fields[8] = "3680"   # charging power W
    fields[9] = "320"    # safe limit x10
    fields[11] = "125"   # house current x10 -> 12.5 A
    fields[18] = "0"     # fault
    fields[19] = "57"    # session energy x10 -> 5.7 kWh
    fields[20] = "42"    # minutes
    for k, v in overrides.items():
        fields[int(k[1:])] = v
    return "$DATA,READ,AD," + ",".join(fields)


def test_parse_app_data():
    app = protocol.parse_app_data(_ad_frame())
    assert app is not None
    assert app.charge_state == protocol.CHARGE_STATE_CHARGING
    assert app.charging and app.car_connected
    assert app.pilot_voltage == 8.9
    assert app.offered_current == 32.0
    assert app.dpm_enabled
    assert app.charging_power == 3.68
    assert app.safe_limit == 32.0
    assert app.house_current == 12.5
    assert app.house_power == 2.88
    assert not app.fault
    assert app.session_energy == 5.7
    assert app.session_minutes == 42


def test_parse_app_data_no_pwm_and_unplugged():
    app = protocol.parse_app_data(_ad_frame(f3="1000", f4="3"))
    assert app.offered_current is None
    assert app.charge_state == protocol.CHARGE_STATE_UNPLUGGED
    assert not app.car_connected


def test_parse_app_data_rejects_short_or_foreign():
    assert protocol.parse_app_data("$DATA,READ,AD,1,2,3") is None
    assert protocol.parse_app_data("$EEP,READ,IDX,174,160") is None


def test_parse_eep_idx():
    assert protocol.parse_eep_idx("$EEP,READ,IDX,174,160") == (174, 160)
    assert protocol.parse_eep_idx("$EEP,WRITE,IDX,158,0") == (158, 0)
    assert protocol.parse_eep_idx("$EEP,READ,IDX,174") is None
    assert protocol.parse_eep_idx("$BLE,AUTH,OK") is None


def test_reply_matching():
    assert protocol.reply_matches("$BLE,AUTH,1234", "$BLE,AUTH,OK")
    assert not protocol.reply_matches("$BLE,AUTH,1234", "$BLE,AUTH,FAIL")
    assert protocol.reply_matches("$EEP,WRITE,IDX,158,0", "$EEP,WRITE,IDX,158,0")
    assert not protocol.reply_matches("$EEP,WRITE,IDX,158,0", "$EEP,WRITE,IDX,158,260")
    assert protocol.reply_matches("$EEP,READ,IDX,158", "$EEP,READ,IDX,158,260")
    assert not protocol.reply_matches("$EEP,READ,IDX,158", "$EEP,READ,IDX,1580,1")
    assert protocol.reply_matches("$DATA,READ,AD", _ad_frame())
    assert not protocol.reply_matches("$CMD,CHARGE,STOP,0", "$CMD,CHARGE,OK")
    assert protocol.reply_matches("$CMD,CHARGE,STOP,0", "$CMD,CHARGE,OK", loose=True)


def test_errors_and_conversions():
    assert protocol.is_error_reply("$ERR,BUSY")
    assert protocol.is_error_reply("$EEP,WRITE,FAIL")
    assert protocol.is_error_reply("$BLE,AUTH,FAIL")
    assert not protocol.is_error_reply("$BLE,AUTH,OK")
    assert protocol.amps_to_raw(16) == 160
    assert protocol.amps_to_raw(6.5) == 65
    assert protocol.raw_to_amps(260) == 26.0
    assert protocol.cmd_write_idx(174, 160) == "$EEP,WRITE,IDX,174,160"
