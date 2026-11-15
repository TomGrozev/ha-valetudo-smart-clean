"""Fixtures for the Home Assistant-layer tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.valetudo_smart_clean.const import (
    CONF_BATTERY,
    CONF_CLEAN_FAN,
    CONF_DAILY_REPORT,
    CONF_DISPATCH_TIMEOUT,
    CONF_DOCK_CONSUMABLES,
    CONF_DOCK_DWELL,
    CONF_DOCK_STATUS,
    CONF_END_SETTLE,
    CONF_ERROR,
    CONF_FAN,
    CONF_GLOBAL_BLOCKERS,
    CONF_MAX_ATTEMPTS,
    CONF_MAX_PRESENCE_GAP,
    CONF_MIN_BATTERY,
    CONF_MIN_PRESENCE,
    CONF_MIN_PRESENCE_FRACTION,
    CONF_MODE,
    CONF_NOTIFY_SCRIPT,
    CONF_PAUSE_TIMEOUT,
    CONF_POSITION_ATTRIBUTE,
    CONF_POSITION_ENTITY,
    CONF_QUIET_TRAVEL,
    CONF_RETRY_DELAY,
    CONF_ROOM_BLOCKERS,
    CONF_ROOM_POSITIONS,
    CONF_ROOMS,
    CONF_STATUS_FLAG,
    CONF_TRAVEL_FAN,
    CONF_VACUUM,
    CONF_WEEKDAYS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DOMAIN,
)

VACUUM = "vacuum.valetudo_willy"
FLAG = "sensor.valetudo_willy_status_flag"
ERROR = "sensor.valetudo_willy_error"
DOCK_STATUS = "sensor.valetudo_willy_dock_status"
BATTERY = "sensor.valetudo_willy_battery_level"
DUSTBAG = "sensor.valetudo_willy_dustbag_dock_component"
FAN = "select.valetudo_willy_fan"
MODE = "select.valetudo_willy_mode"
CAMERA = "camera.willy_camera"
SCRIPT = "script.notify_phone"
HOUSE_QUIET = "binary_sensor.house_quiet"

ROOMS = [
    {"room_id": "kitchen", "name": "Kitchen", "position_values": ["Kitchen"]},
    {"room_id": "toilet", "name": "Toilet", "position_values": ["Toilet"]},
]

BASE_OPTIONS: dict[str, Any] = {
    CONF_WINDOW_START: "00:00:00",
    CONF_WINDOW_END: "23:59:00",
    CONF_WEEKDAYS: [str(day) for day in range(7)],
    CONF_DOCK_DWELL: 0,
    CONF_END_SETTLE: 0,
    CONF_DISPATCH_TIMEOUT: 120,
    CONF_PAUSE_TIMEOUT: 300,
    CONF_RETRY_DELAY: 0,
    CONF_MAX_ATTEMPTS: 2,
    CONF_MIN_BATTERY: 0,
    CONF_MIN_PRESENCE: 1,
    CONF_MIN_PRESENCE_FRACTION: 0.0,
    CONF_MAX_PRESENCE_GAP: 90,
    CONF_QUIET_TRAVEL: False,
    CONF_TRAVEL_FAN: "low",
    CONF_CLEAN_FAN: "medium",
    CONF_DAILY_REPORT: False,
    CONF_GLOBAL_BLOCKERS: [HOUSE_QUIET],
    CONF_ROOM_BLOCKERS: {},
    CONF_ROOM_POSITIONS: {},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom integrations in every test."""
    yield


@pytest.fixture
def areas(hass: HomeAssistant) -> None:
    registry = ar.async_get(hass)
    registry.async_create("Kitchen")
    registry.async_create("Toilet")


@pytest.fixture
def fake_robot(hass: HomeAssistant) -> None:
    """A docked, healthy robot with a map camera attribute."""
    hass.states.async_set(VACUUM, "docked", {"friendly_name": "Willy Robot"})
    hass.states.async_set(FLAG, "none")
    hass.states.async_set(ERROR, "No error", {"friendly_name": "Willy Error"})
    hass.states.async_set(DOCK_STATUS, "idle")
    hass.states.async_set(BATTERY, "98")
    hass.states.async_set(DUSTBAG, "ok")
    hass.states.async_set(
        FAN, "medium", {"options": ["low", "medium", "high", "max"]}
    )
    hass.states.async_set(
        MODE,
        "vacuum",
        {"options": ["vacuum", "mop", "vacuum_and_mop", "vacuum_then_mop"]},
    )
    hass.states.async_set(CAMERA, "idle", {"vacuum_position": {"in_room": "Kitchen"}})
    hass.states.async_set(
        "sensor.valetudo_willy_map_segments",
        "2",
        {"1": "Kitchen", "2": "Toilet"},
    )
    hass.states.async_set(HOUSE_QUIET, "off")


@pytest.fixture
def entry_data() -> dict[str, Any]:
    return {
        CONF_VACUUM: VACUUM,
        CONF_STATUS_FLAG: FLAG,
        CONF_ERROR: ERROR,
        CONF_DOCK_STATUS: DOCK_STATUS,
        CONF_BATTERY: BATTERY,
        CONF_DOCK_CONSUMABLES: [DUSTBAG],
        CONF_FAN: FAN,
        CONF_MODE: MODE,
        CONF_POSITION_ENTITY: CAMERA,
        CONF_POSITION_ATTRIBUTE: "vacuum_position.in_room",
        CONF_NOTIFY_SCRIPT: SCRIPT,
        CONF_ROOMS: [dict(room) for room in ROOMS],
    }


@pytest.fixture
def entry(hass: HomeAssistant, entry_data, areas) -> MockConfigEntry:
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Willy Robot Smart Clean",
        data=entry_data,
        options=dict(BASE_OPTIONS),
        unique_id=VACUUM,
    )
    mock_entry.add_to_hass(hass)
    return mock_entry


@pytest.fixture
async def setup_entry(hass: HomeAssistant, entry, fake_robot):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def unique_id_to_entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    registry = er.async_get(hass)
    return next(
        (
            entity.entity_id
            for entity in registry.entities.values()
            if entity.unique_id == unique_id
        ),
        None,
    )


def device_for_entry(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry | None:
    registry = dr.async_get(hass)
    return next(iter(dr.async_entries_for_config_entry(registry, entry.entry_id)), None)
