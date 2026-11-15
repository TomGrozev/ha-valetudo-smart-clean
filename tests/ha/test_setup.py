"""Setup, entity, storage, notification and teardown tests."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import async_mock_service

from custom_components.valetudo_smart_clean import build_snapshot
from custom_components.valetudo_smart_clean.const import (
    CONF_BLOCKER_RULES,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
)
from custom_components.valetudo_smart_clean.core.models import SkipToday
from tests.ha.conftest import device_for_entry, unique_id_to_entity_id


async def test_setup_creates_device_and_entities(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    device = device_for_entry(hass, entry)
    assert device is not None
    assert device.name == "Willy Robot Smart Clean"

    registry = er.async_get(hass)
    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert unique_ids == {
        f"{entry.entry_id}_smart_clean",
        f"{entry.entry_id}_clean_when_occupied",
        f"{entry.entry_id}_room_auto_kitchen",
        f"{entry.entry_id}_room_auto_toilet",
        f"{entry.entry_id}_status",
        f"{entry.entry_id}_last_job",
        f"{entry.entry_id}_room_kitchen",
        f"{entry.entry_id}_room_toilet",
    }

    status_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_status")
    assert status_id is not None
    assert hass.states.get(status_id).state
    assert hass.states.get(unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")).state == "off"
    # Fixed entities take their names from translations, so ids never collide.
    assert {
        unique_id_to_entity_id(hass, f"{entry.entry_id}_{key}")
        for key in ("smart_clean", "clean_when_occupied", "status", "last_job")
    } == {
        "switch.willy_robot_smart_clean_smart_clean",
        "switch.willy_robot_smart_clean_clean_when_occupied",
        "sensor.willy_robot_smart_clean_status",
        "sensor.willy_robot_smart_clean_last_job",
    }


async def test_unload_removes_listeners(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    runtime = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert runtime._unsubs == []
    assert runtime._stopped


async def test_state_survives_entry_reload(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    async_mock_service(hass, "vacuum", "clean_area")
    switch_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(switch_id).state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    switch_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")
    assert hass.states.get(switch_id).state == "on"


async def test_notification_action_becomes_intent(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    toilet_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_room_toilet")

    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": "VSC_SKIP:toilet"})
    await hass.async_block_till_done()
    assert hass.states.get(toilet_id).state == "skipped"

    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": "VSC_RETRY"})
    await hass.async_block_till_done()
    assert hass.states.get(toilet_id).state != "skipped"


async def test_unknown_notification_action_is_ignored(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    runtime = entry.runtime_data
    before = runtime.state
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": "SOMETHING_ELSE"})
    await hass.async_block_till_done()
    assert runtime.state is before


async def test_rule_entity_change_triggers_reconcile(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    hard_blocker = "binary_sensor.hall_door"
    hass.states.async_set(hard_blocker, "off")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_BLOCKER_RULES: [
                *entry.options[CONF_BLOCKER_RULES],
                {
                    "id": "hall-door",
                    "name": "Hall door open",
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": hard_blocker,
                            "state": "on",
                        }
                    ],
                    "rooms": ["toilet"],
                    "overridable": False,
                },
            ],
        },
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert hard_blocker in runtime._watched_entity_ids()

    calls = 0
    original = runtime.async_reconcile

    async def counting_reconcile() -> None:
        nonlocal calls
        calls += 1
        await original()

    runtime.async_reconcile = counting_reconcile
    hass.states.async_set(hard_blocker, "on")
    await hass.async_block_till_done()
    snapshot = build_snapshot(
        hass, runtime.config, runtime.data, runtime.compiled_rules
    )
    assert snapshot.active_blockers["hall-door"] == "Hall door open"
    assert calls >= 1


async def test_stopped_runtime_ignores_queued_reconcile(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    runtime = entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    before = runtime.state
    await runtime.async_reconcile()
    await runtime.async_intent(SkipToday("toilet"))
    assert runtime.state is before
