"""Config and options flow tests."""

from __future__ import annotations

from datetime import time

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.valetudo_smart_clean.const import (
    CONF_BLOCKER_CONDITIONS,
    CONF_BLOCKER_NAME,
    CONF_BLOCKER_OVERRIDABLE,
    CONF_BLOCKER_ROOMS,
    CONF_BLOCKER_RULES,
    CONF_ROOMS,
    CONF_WINDOW_START,
    DOMAIN,
)


async def test_config_flow_happy_path(hass: HomeAssistant, areas, fake_robot) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"vacuum": "vacuum.valetudo_willy"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "rooms"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ROOMS: ["kitchen", "toilet"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["vacuum"] == "vacuum.valetudo_willy"
    assert data["position_attribute"] == "vacuum_position.in_room"
    assert [room["room_id"] for room in data[CONF_ROOMS]] == ["kitchen", "toilet"]
    assert data[CONF_ROOMS][0]["name"] == "Kitchen"
    assert data[CONF_ROOMS][0]["position_values"] == ["Kitchen"]


async def test_config_flow_rejects_unknown_vacuum(
    hass: HomeAssistant, areas
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"vacuum": "vacuum.missing"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"vacuum": "vacuum_not_found"}


async def test_options_flow_updates_schedule(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "schedule"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "window_start": "09:30:00",
            "window_end": "16:45:00",
            "weekdays": ["0", "1", "5"],
            "mop_weekdays": ["4"],
            "vacuum_mode": "vacuum",
            "mop_mode": "vacuum_then_mop",
        },
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WINDOW_START] == "09:30"
    assert result["data"]["weekdays"] == ["0", "1", "5"]
    assert result["data"]["mop_mode"] == "vacuum_then_mop"


async def test_options_flow_edits_room(hass: HomeAssistant, entry, fake_robot) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rooms"}
    )
    assert result["step_id"] == "rooms"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ROOMS: "toilet"}
    )
    assert result["step_id"] == "room"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"position_values": "Toilet, Guest toilet"},
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["data"]["room_positions"] == {
        "toilet": ["Toilet", "Guest toilet"]
    }


async def _open_blocker_add(hass: HomeAssistant, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "blockers"}
    )
    assert result["step_id"] == "blockers"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "blocker_add"}
    )
    assert result["step_id"] == "blocker_add"
    return result


async def test_options_flow_adds_blocker_rule(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    hass.states.async_set("binary_sensor.child_room_occupancy", "off")
    result = await _open_blocker_add(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BLOCKER_NAME: "Sleeping child",
            CONF_BLOCKER_CONDITIONS: [
                {
                    "condition": "state",
                    "entity_id": "binary_sensor.child_room_occupancy",
                    "state": "off",
                    "for": {"minutes": 10},
                }
            ],
            CONF_BLOCKER_ROOMS: ["toilet"],
            CONF_BLOCKER_OVERRIDABLE: False,
        },
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    rule = result["data"][CONF_BLOCKER_RULES][-1]
    assert rule[CONF_BLOCKER_NAME] == "Sleeping child"
    assert rule[CONF_BLOCKER_ROOMS] == ["toilet"]
    assert rule[CONF_BLOCKER_OVERRIDABLE] is False
    assert len(rule["id"]) == 32
    assert rule[CONF_BLOCKER_CONDITIONS][0]["for"] == "00:10:00"

    # The stored duration survives a reload of the entry and its rules.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    stored = entry.runtime_data.rules[-1]
    assert stored.conditions[0]["for"] == "00:10:00"


async def test_options_flow_rejects_invalid_conditions(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    result = await _open_blocker_add(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BLOCKER_NAME: "Broken",
            CONF_BLOCKER_CONDITIONS: [
                {
                    "condition": "device",
                    "device_id": "not-a-real-device",
                    "domain": "light",
                    "type": "is_on",
                }
            ],
            CONF_BLOCKER_ROOMS: [],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "blocker_add"
    assert result["errors"] == {CONF_BLOCKER_CONDITIONS: "invalid_conditions"}


async def test_options_flow_imports_blocker_rules(
    hass: HomeAssistant, entry, fake_robot
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "blockers"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "blocker_import"}
    )
    assert result["step_id"] == "blocker_import"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BLOCKER_RULES: [
                {
                    CONF_BLOCKER_NAME: "House quiet",
                    CONF_BLOCKER_CONDITIONS: [
                        {
                            "condition": "state",
                            "entity_id": "binary_sensor.house_quiet",
                            "state": "on",
                        }
                    ],
                    CONF_BLOCKER_ROOMS: [],
                }
            ]
        },
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    rules = result["data"][CONF_BLOCKER_RULES]
    assert len(rules) == 1
    assert rules[0][CONF_BLOCKER_NAME] == "House quiet"
    assert rules[0][CONF_BLOCKER_ROOMS] == []
    assert rules[0][CONF_BLOCKER_OVERRIDABLE] is False
