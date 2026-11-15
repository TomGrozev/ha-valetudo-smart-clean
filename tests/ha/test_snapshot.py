"""Snapshot building from fake entity states."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.valetudo_smart_clean import build_config, build_snapshot
from custom_components.valetudo_smart_clean.core.models import VacuumStatus
from tests.ha.conftest import BATTERY, ERROR, HOUSE_QUIET, VACUUM


async def test_snapshot_reads_docked_robot(hass: HomeAssistant, entry, fake_robot) -> None:
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.vacuum is VacuumStatus.DOCKED
    assert snapshot.position == "kitchen"
    assert snapshot.error is None
    assert snapshot.dock_ok is True
    assert snapshot.battery == 98
    assert snapshot.fan == "medium"
    assert snapshot.mode == "vacuum"
    assert snapshot.flag == "none"


async def test_snapshot_maps_position_from_attribute(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    hass.states.async_set(VACUUM, "cleaning")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.vacuum is VacuumStatus.CLEANING
    assert snapshot.position == "kitchen"

    hass.states.async_set(VACUUM, "idle")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.position == "kitchen"


async def test_snapshot_error_and_unavailable(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    hass.states.async_set(ERROR, "Cannot reach target")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.error == "Cannot reach target"

    hass.states.async_set(VACUUM, "unavailable")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.vacuum is VacuumStatus.UNAVAILABLE
    assert snapshot.position == "kitchen"


async def test_snapshot_only_on_counts_as_blocker(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    hass.states.async_set(HOUSE_QUIET, "home")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.active_blockers == {}

    hass.states.async_set(HOUSE_QUIET, "on")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert HOUSE_QUIET in snapshot.active_blockers


async def test_snapshot_battery_unknown(hass: HomeAssistant, entry, fake_robot) -> None:
    hass.states.async_set(BATTERY, "unknown")
    snapshot = build_snapshot(hass, build_config(entry), dict(entry.data))
    assert snapshot.battery is None


async def test_snapshot_includes_hard_blockers(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    from custom_components.valetudo_smart_clean.const import CONF_ROOM_HARD_BLOCKERS

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_ROOM_HARD_BLOCKERS: {"toilet": ["binary_sensor.hamish_asleep"]},
        },
    )
    hass.states.async_set("binary_sensor.hamish_asleep", "on")

    config = build_config(entry)
    assert config.rooms[1].hard_blockers == ("binary_sensor.hamish_asleep",)
    snapshot = build_snapshot(hass, config, dict(entry.data))
    assert "binary_sensor.hamish_asleep" in snapshot.active_blockers
