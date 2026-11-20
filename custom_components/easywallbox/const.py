"""Constants for the EasyWallbox integration."""

from __future__ import annotations

DOMAIN = "easywallbox"

CONF_PIN = "pin"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MAX_CURRENT = "max_current"
CONF_DPM_DEFAULT = "dpm_default"
CONF_PAIR = "pair"

DEFAULT_NAME = "EasyWallbox"
DEFAULT_SCAN_INTERVAL = 30  # seconds, same as upstream ESPHome firmware
DEFAULT_MAX_CURRENT = 32.0  # A
DEFAULT_DPM_DEFAULT = 26.0  # A (~6 kW), used when no previous DPM limit is known
DEFAULT_PAIR = True

MIN_CURRENT = 6.0  # A, IEC 61851 minimum charging current

MANUFACTURER = "eSolutions / Free2Move"
MODEL = "EasyWallbox"

SERVICE_SEND_COMMAND = "send_command"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_COMMAND = "command"
ATTR_TIMEOUT = "timeout"
