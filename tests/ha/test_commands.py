"""Command execution: the core's commands reach the right services."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import async_mock_service

from custom_components.valetudo_smart_clean.const import (
    CONF_VACUUM_MODE,
    DOMAIN,
)
from tests.ha.conftest import (
    ERROR,
    FAN,
    MODE,
    SCRIPT,
    VACUUM,
    unique_id_to_entity_id,
)


async def test_clean_rooms_service_sends_area_ids(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    clean_area = async_mock_service(hass, "vacuum", "clean_area")

    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"rooms": ["toilet"]},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(clean_area) == 1
    assert clean_area[0].data["entity_id"] == VACUUM
    assert clean_area[0].data["cleaning_area_id"] == ["toilet"]


async def test_clean_rooms_service_accepts_a_room_string(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    clean_area = async_mock_service(hass, "vacuum", "clean_area")

    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"rooms": "toilet"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(clean_area) == 1
    assert clean_area[0].data["cleaning_area_id"] == ["toilet"]


async def test_skip_and_retry_services(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    toilet_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_room_toilet")

    await hass.services.async_call(
        DOMAIN, "skip_today", {"room": "toilet"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(toilet_id).state == "skipped"

    await hass.services.async_call(
        DOMAIN, "retry_today", {"room": "toilet"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(toilet_id).state == "waiting"


async def test_auto_dispatch_sets_mode_and_cleans(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_VACUUM_MODE: "vacuum_then_mop"}
    )
    clean_area = async_mock_service(hass, "vacuum", "clean_area")
    select_option = async_mock_service(hass, "select", "select_option")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    switch_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert select_option, "the mode select was not called"
    assert select_option[0].data["entity_id"] == MODE
    assert select_option[0].data["option"] == "vacuum_then_mop"
    assert len(clean_area) == 1
    assert clean_area[0].data["cleaning_area_id"] == ["kitchen", "toilet"]


async def test_stop_service_sends_robot_home(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    clean_area = async_mock_service(hass, "vacuum", "clean_area")
    return_home = async_mock_service(hass, "vacuum", "return_to_base")

    await hass.services.async_call(
        DOMAIN, "clean_rooms", {"rooms": ["toilet"]}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(clean_area) == 1

    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    await hass.async_block_till_done()

    assert len(return_home) == 1
    assert return_home[0].data["entity_id"] == VACUUM


async def test_error_while_running_notifies(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    async_mock_service(hass, "vacuum", "clean_area")
    turn_on = async_mock_service(hass, "script", "turn_on")

    await hass.services.async_call(
        DOMAIN, "clean_rooms", {"rooms": ["toilet"]}, blocking=True
    )
    await hass.async_block_till_done()

    hass.states.async_set(VACUUM, "error")
    hass.states.async_set(ERROR, "Cannot reach target")
    await hass.async_block_till_done()

    assert turn_on, "the notification script was not called"
    call = turn_on[0]
    assert call.data["entity_id"] == SCRIPT
    variables = call.data["variables"]
    assert variables["title"]
    assert "Cannot reach target" in variables["message"] or variables["message"]
    assert variables["level"] == "time-sensitive"
    assert variables["actions"] == []
