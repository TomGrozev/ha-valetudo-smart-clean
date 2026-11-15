"""Blocker rules: evaluation, fail-closed behaviour, dispatch and migration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import async_mock_service

from custom_components.valetudo_smart_clean.const import (
    CONF_BLOCKER_RULES,
    CONF_GLOBAL_BLOCKERS,
    CONF_ROOM_BLOCKERS,
    CONF_ROOM_HARD_BLOCKERS,
)
from tests.ha.conftest import HOUSE_QUIET, unique_id_to_entity_id


def _state_rule(
    rule_id: str,
    *,
    name: str = "Sleeping child",
    entity_id: str = "binary_sensor.child_room",
    state: str = "off",
    for_value: str | None = None,
    rooms: list[str] | None = None,
    overridable: bool = True,
) -> dict:
    condition: dict = {"condition": "state", "entity_id": entity_id, "state": state}
    if for_value is not None:
        condition["for"] = for_value
    return {
        "id": rule_id,
        "name": name,
        "conditions": [condition],
        "rooms": rooms or [],
        "overridable": overridable,
    }


async def _reload_rules(hass: HomeAssistant, entry: ConfigEntry, rules: list[dict]):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_BLOCKER_RULES: rules}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_state_for_rule_turns_active_after_hold(
    hass: HomeAssistant, setup_entry, entry, freezer
) -> None:
    hass.states.async_set("binary_sensor.child_room", "off")
    runtime = await _reload_rules(
        hass,
        entry,
        [_state_rule("sleep", for_value="00:10:00")],
    )

    assert "sleep" not in runtime._snapshot().active_blockers

    freezer.tick(timedelta(minutes=9))
    await runtime.async_reconcile()
    assert "sleep" not in runtime._snapshot().active_blockers

    freezer.tick(timedelta(minutes=1, seconds=1))
    await runtime.async_reconcile()
    assert runtime._snapshot().active_blockers["sleep"] == "Sleeping child"


async def test_rule_template_error_is_active(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    runtime = await _reload_rules(
        hass,
        entry,
        [
            {
                "id": "broken",
                "name": "Broken",
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ 1 / 0 }}",
                    }
                ],
                "rooms": [],
                "overridable": True,
            }
        ],
    )

    snapshot = runtime._snapshot()
    assert snapshot.active_blockers["broken"] == "Broken (rule error)"
    assert runtime.compiled_rules[0].error_logged is True

    # The failure is logged once; the rule keeps blocking while broken.
    runtime._snapshot()
    assert runtime.compiled_rules[0].error_logged is True


async def test_stored_template_rule_evaluates(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    # Options hold templates as plain strings; they must compile to real templates.
    hass.states.async_set("sensor.person", "Study")
    runtime = await _reload_rules(
        hass,
        entry,
        [
            {
                "id": "study",
                "name": "Study is occupied",
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ is_state('sensor.person', 'Study') }}",
                    }
                ],
                "rooms": [],
                "overridable": True,
            }
        ],
    )

    assert runtime._snapshot().active_blockers["study"] == "Study is occupied"
    hass.states.async_set("sensor.person", "Kitchen")
    assert "study" not in runtime._snapshot().active_blockers


async def test_whole_house_rule_blocks_even_when_occupied(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    hass.states.async_set(HOUSE_QUIET, "on")
    await _reload_rules(
        hass,
        entry,
        [_state_rule("house", name="House quiet", entity_id=HOUSE_QUIET, state="on")],
    )
    clean_area = async_mock_service(hass, "vacuum", "clean_area")
    occupied_id = unique_id_to_entity_id(
        hass, f"{entry.entry_id}_clean_when_occupied"
    )
    smart_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": occupied_id}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": smart_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.state.clean_when_occupied is True
    assert clean_area == []


async def test_non_overridable_room_rule_blocks_when_occupied(
    hass: HomeAssistant, setup_entry, entry
) -> None:
    hass.states.async_set("binary_sensor.child_room", "on")
    await _reload_rules(
        hass,
        entry,
        [
            _state_rule(
                "sleep",
                entity_id="binary_sensor.child_room",
                state="on",
                rooms=["kitchen", "toilet"],
                overridable=False,
            )
        ],
    )
    clean_area = async_mock_service(hass, "vacuum", "clean_area")
    occupied_id = unique_id_to_entity_id(
        hass, f"{entry.entry_id}_clean_when_occupied"
    )
    smart_id = unique_id_to_entity_id(hass, f"{entry.entry_id}_smart_clean")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": occupied_id}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": smart_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert clean_area == []


async def test_migrates_legacy_entity_blockers(
    hass: HomeAssistant, entry, fake_robot, entry_data, areas
) -> None:
    from pytest_homeassistant.common import MockConfigEntry

    hass.states.async_set(
        HOUSE_QUIET, "off", {"friendly_name": "House Quiet Sensor"}
    )
    hass.states.async_set(
        "binary_sensor.hall_door", "off", {"friendly_name": "Hall Door"}
    )
    options = {
        **entry.options,
        CONF_GLOBAL_BLOCKERS: [HOUSE_QUIET],
        CONF_ROOM_BLOCKERS: {"kitchen": [HOUSE_QUIET], "toilet": [HOUSE_QUIET]},
        CONF_ROOM_HARD_BLOCKERS: {"toilet": ["binary_sensor.hall_door"]},
    }
    options.pop(CONF_BLOCKER_RULES)
    legacy = MockConfigEntry(
        domain="valetudo_smart_clean",
        title="Willy Robot Smart Clean",
        data=entry_data,
        options=options,
        unique_id="vacuum.migrated",
        version=1,
    )
    legacy.add_to_hass(hass)

    assert await hass.config_entries.async_setup(legacy.entry_id)
    await hass.async_block_till_done()

    assert legacy.version == 2
    assert legacy.minor_version == 1
    assert CONF_GLOBAL_BLOCKERS not in legacy.options
    rules = legacy.runtime_data.rules
    assert len(rules) == 3

    house = next(rule for rule in rules if rule.whole_house)
    assert house.name == "House Quiet Sensor"
    assert house.overridable is False

    soft = next(rule for rule in rules if rule.rooms and rule.effective_overridable)
    assert soft.rooms == ("kitchen", "toilet")

    hard = next(rule for rule in rules if rule.rooms and not rule.effective_overridable)
    assert hard.rooms == ("toilet",)
    assert hard.name == "Hall Door"
    assert hard.conditions[0]["entity_id"] == "binary_sensor.hall_door"
