"""Config flow and entity tests for EasyWallbox."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easywallbox.const import CONF_PIN, DOMAIN

from .conftest import ADDRESS, PIN


async def test_manual_flow(hass: HomeAssistant, fake_wallbox) -> None:
    """Manual MAC + PIN creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    # No discovered wallboxes -> manual step.
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "zz"}
    )
    assert result["errors"] == {"base": "invalid_mac"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS.lower()}
    )
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "12"}
    )
    assert result["errors"] == {"base": "invalid_pin"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_PIN: PIN}
    await hass.async_block_till_done()


async def test_flow_invalid_auth(hass: HomeAssistant, fake_wallbox) -> None:
    """A rejected PIN is reported."""
    original = fake_wallbox.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.reject_pin = True

    fake_wallbox.__init__ = init
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
        assert result["errors"] == {"base": "invalid_auth"}
    finally:
        fake_wallbox.__init__ = original


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        title="EasyWallbox",
        data={CONF_ADDRESS: ADDRESS, CONF_PIN: PIN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{ADDRESS.lower()}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


async def test_entities_and_controls(hass: HomeAssistant, fake_wallbox) -> None:
    """Entities reflect telemetry and controls write the right registers."""
    entry = await _setup(hass)
    wallbox = fake_wallbox.instances[-1]

    assert hass.states.get(_eid(hass, "sensor", "charge_state")).state == "charging"
    assert hass.states.get(_eid(hass, "sensor", "charging_power")).state == "3.68"
    assert hass.states.get(_eid(hass, "sensor", "session_energy")).state == "5.7"
    assert hass.states.get(_eid(hass, "sensor", "charge_current_set")).state == "16.0"
    assert hass.states.get(_eid(hass, "binary_sensor", "connected")).state == "on"
    assert hass.states.get(_eid(hass, "binary_sensor", "car_connected")).state == "on"
    switch = _eid(hass, "switch", "charging_enabled")
    assert hass.states.get(switch).state == "on"

    # Pause -> DPM 0
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert wallbox.sent[-1] == "$EEP,WRITE,IDX,158,0"
    assert hass.states.get(switch).state == "off"

    # Resume -> last valid DPM limit (26 A)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert wallbox.sent[-1] == "$EEP,WRITE,IDX,158,260"
    assert hass.states.get(switch).state == "on"

    # Charge current slider
    number = _eid(hass, "number", "charge_current")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 10}, blocking=True
    )
    assert wallbox.sent[-1] == "$EEP,WRITE,IDX,174,100"
    assert hass.states.get(number).state == "10.0"

    # Raw command service with response
    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"config_entry_id": entry.entry_id, "command": "$EEP,READ,IDX,156"},
        blocking=True,
        return_response=True,
    )
    assert response == {"reply": "$EEP,READ,IDX,156,0"}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not wallbox.authenticated
