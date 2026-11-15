"""Config and options flows for Valetudo Smart Clean."""

from __future__ import annotations

import logging
from datetime import time
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    BooleanSelector,
    ConditionSelector,
    ConditionSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_BATTERY,
    CONF_BLOCKER_CONDITIONS,
    CONF_BLOCKER_ID,
    CONF_BLOCKER_NAME,
    CONF_BLOCKER_OVERRIDABLE,
    CONF_BLOCKER_ROOMS,
    CONF_BLOCKER_RULES,
    CONF_CLEAN_FAN,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    CONF_DAILY_REPORT,
    CONF_DISPATCH_TIMEOUT,
    CONF_DOCK_CONSUMABLES,
    CONF_DOCK_DWELL,
    CONF_DOCK_STATUS,
    CONF_END_SETTLE,
    CONF_ERROR,
    CONF_FAN,
    CONF_MAP_SEGMENTS,
    CONF_MAX_ATTEMPTS,
    CONF_MAX_PRESENCE_GAP,
    CONF_MIN_BATTERY,
    CONF_MIN_PRESENCE,
    CONF_MIN_PRESENCE_FRACTION,
    CONF_MODE,
    CONF_MOP_MODE,
    CONF_MOP_WEEKDAYS,
    CONF_NOTIFY_SCRIPT,
    CONF_PAUSE_TIMEOUT,
    CONF_POSITION_ATTRIBUTE,
    CONF_POSITION_ENTITY,
    CONF_POSITION_VALUES,
    CONF_QUIET_TRAVEL,
    CONF_RESUME_TIMEOUT,
    CONF_RETRY_DELAY,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOM_POSITIONS,
    CONF_ROOMS,
    CONF_STATUS_FLAG,
    CONF_TRAVEL_FAN,
    CONF_VACUUM,
    CONF_VACUUM_MODE,
    CONF_WEEKDAYS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_CLEAN_FAN,
    DEFAULT_DAILY_REPORT,
    DEFAULT_DISPATCH_TIMEOUT,
    DEFAULT_DOCK_DWELL,
    DEFAULT_END_SETTLE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_PRESENCE_GAP,
    DEFAULT_MIN_BATTERY,
    DEFAULT_MIN_PRESENCE,
    DEFAULT_MIN_PRESENCE_FRACTION,
    DEFAULT_PAUSE_TIMEOUT,
    DEFAULT_POSITION_ATTRIBUTE,
    DEFAULT_RESUME_TIMEOUT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TRAVEL_FAN,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    WEEKDAY_NAMES,
)
from .rules import async_validate_rule_conditions, jsonable_conditions
from .values import parse_time, parse_weekdays

_LOGGER = logging.getLogger(__name__)

NO_MODE = SelectOptionDict(value="", label="Leave unchanged")


def _time_value(options: dict[str, Any], key: str, default: time) -> str:
    """TimeSelector sends and expects "HH:MM:SS" strings, not time objects."""
    return parse_time(options.get(key), default).isoformat(timespec="seconds")


def _weekday_options() -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=str(index), label=name.capitalize())
        for index, name in enumerate(WEEKDAY_NAMES)
    ]


def _weekday_values(options: dict[str, Any], key: str, default: list[str]) -> list[str]:
    return [str(day) for day in parse_weekdays(options.get(key), frozenset(default))]


class ValetudoSmartCleanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Three steps: the robot, the entities read from it, and the rooms."""

    VERSION = CONFIG_ENTRY_VERSION
    MINOR_VERSION = CONFIG_ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self.hass.states.get(user_input[CONF_VACUUM]) is None:
                errors[CONF_VACUUM] = "vacuum_not_found"
            else:
                await self.async_set_unique_id(user_input[CONF_VACUUM])
                self._abort_if_unique_id_configured()
                self._data.update(user_input)
                return await self.async_step_entities()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VACUUM): EntitySelector(
                        EntitySelectorConfig(domain="vacuum")
                    ),
                    vol.Optional(CONF_NOTIFY_SCRIPT): EntitySelector(
                        EntitySelectorConfig(domain="script")
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovered = self._discover()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_rooms()
        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_STATUS_FLAG,
                        description={"suggested_value": discovered.get(CONF_STATUS_FLAG)},
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_ERROR,
                        description={"suggested_value": discovered.get(CONF_ERROR)},
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_DOCK_STATUS,
                        description={
                            "suggested_value": discovered.get(CONF_DOCK_STATUS)
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_BATTERY,
                        description={"suggested_value": discovered.get(CONF_BATTERY)},
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_DOCK_CONSUMABLES,
                        description={
                            "suggested_value": discovered.get(CONF_DOCK_CONSUMABLES)
                        },
                    ): EntitySelector(
                        EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                    vol.Optional(
                        CONF_FAN,
                        description={"suggested_value": discovered.get(CONF_FAN)},
                    ): EntitySelector(EntitySelectorConfig(domain="select")),
                    vol.Optional(
                        CONF_MODE,
                        description={"suggested_value": discovered.get(CONF_MODE)},
                    ): EntitySelector(EntitySelectorConfig(domain="select")),
                    vol.Optional(
                        CONF_POSITION_ENTITY,
                        description={
                            "suggested_value": discovered.get(CONF_POSITION_ENTITY)
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="camera")),
                    vol.Optional(
                        CONF_POSITION_ATTRIBUTE,
                        default=DEFAULT_POSITION_ATTRIBUTE,
                    ): TextSelector(),
                }
            ),
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            area_ids: list[str] = user_input.get(CONF_ROOMS, [])
            if not area_ids:
                errors[CONF_ROOMS] = "no_rooms"
            else:
                rooms = self._build_rooms(area_ids)
                title_state = self.hass.states.get(self._data[CONF_VACUUM])
                title = (
                    f"{title_state.name} Smart Clean"
                    if title_state is not None
                    else "Valetudo Smart Clean"
                )
                return self.async_create_entry(
                    title=title, data={**self._data, CONF_ROOMS: rooms}
                )
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOMS): AreaSelector(
                        AreaSelectorConfig(multiple=True, reorder=True)
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return ValetudoSmartCleanOptionsFlow()

    # -- discovery --------------------------------------------------------

    @callback
    def _discover(self) -> dict[str, Any]:
        """Pre-fill from the entities of the chosen vacuum's device."""
        registry = er.async_get(self.hass)
        vacuum_entry = registry.async_get(self._data.get(CONF_VACUUM, ""))
        if vacuum_entry is None or vacuum_entry.device_id is None:
            return {}
        entities = er.async_entries_for_device(
            registry, vacuum_entry.device_id, include_disabled_entities=True
        )
        found: dict[str, Any] = {}
        consumables: list[str] = []
        for entity in entities:
            entity_id = entity.entity_id
            if entity_id.endswith("_status_flag"):
                found[CONF_STATUS_FLAG] = entity_id
            elif entity_id.endswith("_error"):
                found[CONF_ERROR] = entity_id
            elif entity_id.endswith("_dock_status"):
                found[CONF_DOCK_STATUS] = entity_id
            elif entity_id.endswith(("_battery_level", "_battery")):
                found[CONF_BATTERY] = entity_id
            elif "_dock_component" in entity_id:
                consumables.append(entity_id)
            elif entity_id.endswith("_map_segments"):
                found[CONF_MAP_SEGMENTS] = entity_id
            elif entity.domain == "select":
                if entity_id.endswith("_fan"):
                    found[CONF_FAN] = entity_id
                elif entity_id.endswith("_mode"):
                    found[CONF_MODE] = entity_id
            elif entity.domain == "camera" and CONF_POSITION_ENTITY not in found:
                found[CONF_POSITION_ENTITY] = entity_id
        if consumables:
            found[CONF_DOCK_CONSUMABLES] = sorted(consumables)
        return found

    @callback
    def _build_rooms(self, area_ids: list[str]) -> list[dict[str, Any]]:
        areas = ar.async_get(self.hass)
        segments = self._segment_names()
        area_mapping = self._area_mapping()
        rooms: list[dict[str, Any]] = []
        for area_id in area_ids:
            area = areas.async_get_area(area_id)
            name = area.name if area is not None else area_id
            values = [
                segments[segment_id]
                for segment_id in area_mapping.get(area_id, [])
                if segment_id in segments
            ]
            if not values:
                values = [
                    segment
                    for segment in segments.values()
                    if segment.casefold() == name.casefold()
                ]
            rooms.append(
                {
                    CONF_ROOM_ID: area_id,
                    CONF_ROOM_NAME: name,
                    CONF_POSITION_VALUES: values,
                }
            )
        return rooms

    @callback
    def _area_mapping(self) -> dict[str, list[str]]:
        """Area id -> segment ids, as mapped on the vacuum entity itself."""
        registry = er.async_get(self.hass)
        entity = registry.async_get(self._data.get(CONF_VACUUM, ""))
        if entity is None:
            return {}
        mapping = entity.options.get("vacuum", {}).get("area_mapping") or {}
        return {
            area_id: [str(segment) for segment in segment_ids]
            for area_id, segment_ids in mapping.items()
        }

    @callback
    def _segment_names(self) -> dict[str, str]:
        """Segment id -> display name from the Valetudo map segments sensor."""
        discovered = self._discover()
        entity_id = discovered.get(CONF_MAP_SEGMENTS) or self._data.get(
            CONF_MAP_SEGMENTS
        )
        if not entity_id:
            entity_id = next(
                (
                    candidate
                    for candidate in self.hass.states.async_entity_ids("sensor")
                    if candidate.endswith("_map_segments")
                ),
                None,
            )
        if not entity_id:
            return {}
        state = self.hass.states.get(entity_id)
        if state is None:
            return {}
        return {
            str(key): str(value)
            for key, value in state.attributes.items()
            if key not in {"friendly_name", "unit_of_measurement", "icon"}
        }


class ValetudoSmartCleanOptionsFlow(OptionsFlow):
    """Schedule, behaviour, room positions and named blocker rules."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] | None = None
        self._room_id: str | None = None
        self._rule_id: str | None = None

    @property
    def _options(self) -> dict[str, Any]:
        if self._pending is None:
            self._pending = dict(self.config_entry.options)
        return self._pending

    @property
    def _rooms(self) -> list[dict[str, Any]]:
        return list(self.config_entry.data.get(CONF_ROOMS, []))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["schedule", "behaviour", "rooms", "blockers", "finish"],
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=self._options)

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(
                {
                    CONF_WINDOW_START: parse_time(
                        user_input[CONF_WINDOW_START], DEFAULT_WINDOW_START
                    ).isoformat(timespec="minutes"),
                    CONF_WINDOW_END: parse_time(
                        user_input[CONF_WINDOW_END], DEFAULT_WINDOW_END
                    ).isoformat(timespec="minutes"),
                    CONF_WEEKDAYS: [str(day) for day in user_input[CONF_WEEKDAYS]],
                    CONF_MOP_WEEKDAYS: [
                        str(day) for day in user_input.get(CONF_MOP_WEEKDAYS, [])
                    ],
                    CONF_VACUUM_MODE: user_input.get(CONF_VACUUM_MODE, ""),
                    CONF_MOP_MODE: user_input.get(CONF_MOP_MODE, ""),
                }
            )
            return await self.async_step_init()
        options = self._options
        mode_options = self._mode_options()
        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WINDOW_START,
                        default=_time_value(
                            options, CONF_WINDOW_START, DEFAULT_WINDOW_START
                        ),
                    ): TimeSelector(),
                    vol.Required(
                        CONF_WINDOW_END,
                        default=_time_value(options, CONF_WINDOW_END, DEFAULT_WINDOW_END),
                    ): TimeSelector(),
                    vol.Required(
                        CONF_WEEKDAYS,
                        default=_weekday_values(
                            options, CONF_WEEKDAYS, [str(day) for day in range(7)]
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(options=_weekday_options(), multiple=True)
                    ),
                    vol.Optional(
                        CONF_MOP_WEEKDAYS,
                        default=_weekday_values(options, CONF_MOP_WEEKDAYS, []),
                    ): SelectSelector(
                        SelectSelectorConfig(options=_weekday_options(), multiple=True)
                    ),
                    vol.Optional(
                        CONF_VACUUM_MODE,
                        default=options.get(CONF_VACUUM_MODE, ""),
                    ): SelectSelector(SelectSelectorConfig(options=mode_options)),
                    vol.Optional(
                        CONF_MOP_MODE,
                        default=options.get(CONF_MOP_MODE, ""),
                    ): SelectSelector(SelectSelectorConfig(options=mode_options)),
                }
            ),
        )

    async def async_step_behaviour(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_init()
        options = self._options
        fan_options = self._fan_options()

        def number(minimum: float, maximum: float, step: float) -> NumberSelector:
            return NumberSelector(
                NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    step=step,
                    mode=NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="behaviour",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MIN_BATTERY,
                        default=int(options.get(CONF_MIN_BATTERY, DEFAULT_MIN_BATTERY)),
                    ): number(0, 100, 1),
                    vol.Required(
                        CONF_MAX_ATTEMPTS,
                        default=int(
                            options.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)
                        ),
                    ): number(1, 10, 1),
                    vol.Required(
                        CONF_DOCK_DWELL,
                        default=int(options.get(CONF_DOCK_DWELL, DEFAULT_DOCK_DWELL)),
                    ): number(0, 3600, 10),
                    vol.Required(
                        CONF_END_SETTLE,
                        default=int(options.get(CONF_END_SETTLE, DEFAULT_END_SETTLE)),
                    ): number(0, 600, 10),
                    vol.Required(
                        CONF_DISPATCH_TIMEOUT,
                        default=int(
                            options.get(CONF_DISPATCH_TIMEOUT, DEFAULT_DISPATCH_TIMEOUT)
                        ),
                    ): number(30, 1800, 10),
                    vol.Required(
                        CONF_PAUSE_TIMEOUT,
                        default=int(
                            options.get(CONF_PAUSE_TIMEOUT, DEFAULT_PAUSE_TIMEOUT)
                        ),
                    ): number(60, 3600, 30),
                    vol.Required(
                        CONF_RESUME_TIMEOUT,
                        default=int(
                            options.get(CONF_RESUME_TIMEOUT, DEFAULT_RESUME_TIMEOUT)
                        ),
                    ): number(60, 21600, 60),
                    vol.Required(
                        CONF_RETRY_DELAY,
                        default=int(options.get(CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY)),
                    ): number(60, 14400, 60),
                    vol.Required(
                        CONF_MIN_PRESENCE,
                        default=int(
                            options.get(CONF_MIN_PRESENCE, DEFAULT_MIN_PRESENCE)
                        ),
                    ): number(0, 600, 5),
                    vol.Required(
                        CONF_MIN_PRESENCE_FRACTION,
                        default=float(
                            options.get(
                                CONF_MIN_PRESENCE_FRACTION,
                                DEFAULT_MIN_PRESENCE_FRACTION,
                            )
                        ),
                    ): number(0, 1, 0.05),
                    vol.Required(
                        CONF_MAX_PRESENCE_GAP,
                        default=int(
                            options.get(CONF_MAX_PRESENCE_GAP, DEFAULT_MAX_PRESENCE_GAP)
                        ),
                    ): number(15, 600, 5),
                    vol.Required(
                        CONF_QUIET_TRAVEL,
                        default=bool(options.get(CONF_QUIET_TRAVEL, False)),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_TRAVEL_FAN,
                        default=str(
                            options.get(CONF_TRAVEL_FAN, DEFAULT_TRAVEL_FAN)
                        ),
                    ): SelectSelector(SelectSelectorConfig(options=fan_options)),
                    vol.Required(
                        CONF_CLEAN_FAN,
                        default=str(options.get(CONF_CLEAN_FAN, DEFAULT_CLEAN_FAN)),
                    ): SelectSelector(SelectSelectorConfig(options=fan_options)),
                    vol.Required(
                        CONF_DAILY_REPORT,
                        default=bool(
                            options.get(CONF_DAILY_REPORT, DEFAULT_DAILY_REPORT)
                        ),
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._rooms:
            return await self.async_step_init()
        if user_input is not None:
            self._room_id = user_input[CONF_ROOMS]
            return await self.async_step_room()
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOMS): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=room[CONF_ROOM_ID],
                                    label=room.get(CONF_ROOM_NAME, room[CONF_ROOM_ID]),
                                )
                                for room in self._rooms
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._room_id is not None
        room = next(
            (room for room in self._rooms if room[CONF_ROOM_ID] == self._room_id),
            None,
        )
        positions = self._options.get(CONF_ROOM_POSITIONS, {})
        if user_input is not None:
            positions = {**positions, self._room_id: _split_values(user_input)}
            self._options[CONF_ROOM_POSITIONS] = positions
            return await self.async_step_init()
        current_positions = positions.get(
            self._room_id,
            room.get(CONF_POSITION_VALUES, []) if room is not None else [],
        )
        return self.async_show_form(
            step_id="room",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POSITION_VALUES,
                        default=", ".join(current_positions),
                    ): TextSelector(),
                }
            ),
            description_placeholders={
                "room": room.get(CONF_ROOM_NAME, self._room_id)
                if room is not None
                else self._room_id
            },
        )

    async def async_step_blockers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="blockers",
            menu_options=[
                "blocker_add",
                "blocker_edit",
                "blocker_delete",
                "blocker_import",
                "blocker_back",
            ],
        )

    async def async_step_blocker_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_init()

    async def async_step_blocker_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_rule_form(
            user_input, step_id="blocker_add", rule=None
        )

    async def async_step_blocker_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._rule_id = str(user_input[CONF_BLOCKER_ID])
            return await self.async_step_blocker_edit_form()
        return self._show_rule_picker("blocker_edit")

    async def async_step_blocker_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        rule = next(
            (
                rule
                for rule in self._stored_rules()
                if str(rule.get(CONF_BLOCKER_ID)) == self._rule_id
            ),
            None,
        )
        if rule is None:
            return await self.async_step_blockers()
        return await self._async_rule_form(
            user_input, step_id="blocker_edit_form", rule=rule
        )

    async def async_step_blocker_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            rule_id = str(user_input[CONF_BLOCKER_ID])
            self._save_rules(
                [
                    rule
                    for rule in self._stored_rules()
                    if str(rule.get(CONF_BLOCKER_ID)) != rule_id
                ]
            )
            return await self.async_step_init()
        return self._show_rule_picker("blocker_delete")

    async def async_step_blocker_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = user_input.get(CONF_BLOCKER_RULES)
            imported: list[dict[str, Any]] = []
            if not isinstance(raw, list) or not raw:
                errors[CONF_BLOCKER_RULES] = "invalid_rules"
            else:
                for entry in raw:
                    if not isinstance(entry, dict):
                        errors[CONF_BLOCKER_RULES] = "invalid_rules"
                        break
                    name = str(entry.get(CONF_BLOCKER_NAME) or "").strip()
                    conditions = entry.get(CONF_BLOCKER_CONDITIONS) or []
                    rooms = entry.get(CONF_BLOCKER_ROOMS) or []
                    if (
                        not name
                        or not isinstance(conditions, (list, tuple))
                        or not isinstance(rooms, (list, tuple))
                    ):
                        errors[CONF_BLOCKER_RULES] = "invalid_rules"
                        break
                    try:
                        await async_validate_rule_conditions(self.hass, conditions)
                    except Exception:
                        errors[CONF_BLOCKER_RULES] = "invalid_conditions"
                        break
                    room_ids = [str(room) for room in rooms]
                    imported.append(
                        {
                            CONF_BLOCKER_ID: uuid4().hex,
                            CONF_BLOCKER_NAME: name,
                            CONF_BLOCKER_CONDITIONS: jsonable_conditions(
                                list(conditions)
                            ),
                            CONF_BLOCKER_ROOMS: room_ids,
                            CONF_BLOCKER_OVERRIDABLE: bool(
                                entry.get(CONF_BLOCKER_OVERRIDABLE, True)
                            )
                            and bool(room_ids),
                        }
                    )
            if not errors:
                replaced = {
                    str(rule.get(CONF_BLOCKER_NAME)) for rule in imported
                }
                self._save_rules(
                    [
                        rule
                        for rule in self._stored_rules()
                        if str(rule.get(CONF_BLOCKER_NAME)) not in replaced
                    ]
                    + imported
                )
                return await self.async_step_init()
        return self.async_show_form(
            step_id="blocker_import",
            data_schema=vol.Schema(
                {vol.Required(CONF_BLOCKER_RULES): ObjectSelector()}
            ),
            errors=errors,
        )

    # -- blocker helpers --------------------------------------------------

    @callback
    def _stored_rules(self) -> list[dict[str, Any]]:
        rules = self._options.get(CONF_BLOCKER_RULES, [])
        return [dict(rule) for rule in rules if isinstance(rule, dict)]

    @callback
    def _save_rules(self, rules: list[dict[str, Any]]) -> None:
        self._options[CONF_BLOCKER_RULES] = rules

    @callback
    def _show_rule_picker(self, step_id: str) -> ConfigFlowResult:
        rules = self._stored_rules()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BLOCKER_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=str(rule.get(CONF_BLOCKER_ID)),
                                    label=str(
                                        rule.get(CONF_BLOCKER_NAME)
                                        or rule.get(CONF_BLOCKER_ID)
                                    ),
                                )
                                for rule in rules
                            ]
                        )
                    )
                }
            ),
        )

    @callback
    def _rule_schema(self, source: dict[str, Any] | None) -> vol.Schema:
        source = source or {}
        room_ids = [room[CONF_ROOM_ID] for room in self._rooms]
        rooms = [
            str(room)
            for room in source.get(CONF_BLOCKER_ROOMS, []) or []
            if str(room) in room_ids
        ]
        return vol.Schema(
            {
                vol.Required(
                    CONF_BLOCKER_NAME,
                    default=str(source.get(CONF_BLOCKER_NAME) or ""),
                ): TextSelector(),
                vol.Required(
                    CONF_BLOCKER_CONDITIONS,
                    default=list(source.get(CONF_BLOCKER_CONDITIONS) or []),
                ): ConditionSelector(ConditionSelectorConfig()),
                vol.Optional(CONF_BLOCKER_ROOMS, default=rooms): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=room[CONF_ROOM_ID],
                                label=room.get(CONF_ROOM_NAME, room[CONF_ROOM_ID]),
                            )
                            for room in self._rooms
                        ],
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_BLOCKER_OVERRIDABLE,
                    default=bool(source.get(CONF_BLOCKER_OVERRIDABLE, True)),
                ): BooleanSelector(),
            }
        )

    async def _async_rule_form(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        rule: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_BLOCKER_NAME) or "").strip()
            conditions = user_input.get(CONF_BLOCKER_CONDITIONS) or []
            if not name:
                errors[CONF_BLOCKER_NAME] = "name_required"
            if not errors:
                try:
                    await async_validate_rule_conditions(self.hass, conditions)
                except Exception:
                    errors[CONF_BLOCKER_CONDITIONS] = "invalid_conditions"
            if not errors:
                rooms = [
                    str(room) for room in user_input.get(CONF_BLOCKER_ROOMS) or []
                ]
                new_rule = {
                    CONF_BLOCKER_ID: str(
                        (rule or {}).get(CONF_BLOCKER_ID) or uuid4().hex
                    ),
                    CONF_BLOCKER_NAME: name,
                    CONF_BLOCKER_CONDITIONS: jsonable_conditions(list(conditions)),
                    CONF_BLOCKER_ROOMS: rooms,
                    # A whole-house rule is never overridable.
                    CONF_BLOCKER_OVERRIDABLE: bool(
                        user_input.get(CONF_BLOCKER_OVERRIDABLE, True)
                    )
                    and bool(rooms),
                }
                rules = self._stored_rules()
                if rule is None:
                    rules.append(new_rule)
                else:
                    rule_id = new_rule[CONF_BLOCKER_ID]
                    rules = [
                        new_rule
                        if str(existing.get(CONF_BLOCKER_ID)) == rule_id
                        else existing
                        for existing in rules
                    ]
                self._save_rules(rules)
                return await self.async_step_init()
        source = user_input if user_input is not None else rule
        return self.async_show_form(
            step_id=step_id,
            data_schema=self._rule_schema(source),
            errors=errors,
        )

    # -- helpers ----------------------------------------------------------

    @callback
    def _select_options(self, key: str) -> list[str]:
        entity_id = self.config_entry.data.get(key)
        if not entity_id:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        options = state.attributes.get("options", [])
        return [str(option) for option in options]

    @callback
    def _mode_options(self) -> list[SelectOptionDict]:
        modes = self._select_options(CONF_MODE)
        return [NO_MODE] + [
            SelectOptionDict(value=mode, label=mode) for mode in modes
        ]

    @callback
    def _fan_options(self) -> list[str]:
        fans = self._select_options(CONF_FAN)
        return fans or [DEFAULT_TRAVEL_FAN, DEFAULT_CLEAN_FAN]


def _split_values(user_input: dict[str, Any]) -> list[str]:
    raw = user_input.get(CONF_POSITION_VALUES, "")
    if not isinstance(raw, str):
        return []
    return [value.strip() for value in raw.split(",") if value.strip()]
