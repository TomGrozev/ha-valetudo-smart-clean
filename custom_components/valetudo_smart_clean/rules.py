"""Named blocker rules built from Home Assistant's condition engine.

A rule is a list of automation conditions plus a scope. All conditions must
pass for the rule to be active. The rules are compiled once per config entry so
the 15-second tick only has to run the checkers, not rebuild them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.condition import (
    ConditionChecker,
    async_extract_entities,
    async_from_config,
    async_validate_conditions_config,
)

from .const import (
    CONF_BLOCKER_CONDITIONS,
    CONF_BLOCKER_ID,
    CONF_BLOCKER_NAME,
    CONF_BLOCKER_OVERRIDABLE,
    CONF_BLOCKER_ROOMS,
    CONF_BLOCKER_RULES,
    CONF_GLOBAL_BLOCKERS,
    CONF_ROOM_BLOCKERS,
    CONF_ROOM_HARD_BLOCKERS,
    CONF_ROOM_ID,
)

_LOGGER = logging.getLogger(__name__)

STATE_CONDITION: Callable[[str], dict[str, Any]] = lambda entity_id: {
    "condition": "state",
    "entity_id": entity_id,
    "state": "on",
}
"""Legacy 0.1.x blockers were simple "this entity is on" conditions."""


@dataclass(frozen=True)
class BlockingRule:
    """One named rule, exactly as stored in the options."""

    id: str
    name: str
    conditions: tuple[Any, ...]
    rooms: tuple[str, ...]
    """Room ids the rule applies to; empty means the whole house."""
    overridable: bool
    """False keeps the rule active even with clean_when_occupied on. Whole-house
    rules are never overridable, whatever the stored flag says."""

    @property
    def whole_house(self) -> bool:
        return not self.rooms

    @property
    def effective_overridable(self) -> bool:
        return self.overridable and not self.whole_house


@dataclass
class CompiledRule:
    """A rule whose conditions are live Home Assistant checkers."""

    rule: BlockingRule
    checks: tuple[ConditionChecker, ...]
    error_logged: bool = False
    """The failure has already been logged; reset when a check completes."""

    @property
    def id(self) -> str:
        return self.rule.id

    @property
    def name(self) -> str:
        return self.rule.name

    def async_check(self) -> bool:
        """Return True when every condition passes. Errors propagate."""
        for check in self.checks:
            if check.async_check() is False:
                self.error_logged = False
                return False
        self.error_logged = False
        return True


def _as_conditions(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _as_rooms(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(room) for room in value if room))


def rules_from_options(options: Mapping[str, Any]) -> tuple[BlockingRule, ...]:
    """Normalise the stored rule list, generating ids for hand-written rules."""
    raw_rules = options.get(CONF_BLOCKER_RULES, [])
    if not isinstance(raw_rules, list):
        return ()
    rules: list[BlockingRule] = []
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get(CONF_BLOCKER_NAME) or "Blocker")
        rules.append(
            BlockingRule(
                id=str(raw.get(CONF_BLOCKER_ID) or uuid4().hex),
                name=name,
                conditions=_as_conditions(raw.get(CONF_BLOCKER_CONDITIONS)),
                rooms=_as_rooms(raw.get(CONF_BLOCKER_ROOMS)),
                overridable=bool(raw.get(CONF_BLOCKER_OVERRIDABLE, True)),
            )
        )
    return tuple(rules)


def rule_to_options(rule: BlockingRule) -> dict[str, Any]:
    """Serialise one rule back to the options shape."""
    return {
        CONF_BLOCKER_ID: rule.id,
        CONF_BLOCKER_NAME: rule.name,
        CONF_BLOCKER_CONDITIONS: list(rule.conditions),
        CONF_BLOCKER_ROOMS: list(rule.rooms),
        CONF_BLOCKER_OVERRIDABLE: rule.effective_overridable,
    }


def jsonable_conditions(value: Any) -> Any:
    """Return conditions the options store can persist.

    The condition selector turns ``for: {minutes: 10}`` into a timedelta; the
    stored rule keeps it as ``for: "00:10:00"``.
    """
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        sign = "-" if total < 0 else ""
        total = abs(total)
        return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    if isinstance(value, (datetime, time, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: jsonable_conditions(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable_conditions(item) for item in value]
    return value


async def async_validate_rule_conditions(
    hass: HomeAssistant, conditions: Any
) -> list[Any]:
    """Validate conditions the way automations do; raises on invalid config."""
    return await async_validate_conditions_config(hass, list(_as_conditions(conditions)))


async def async_compile_rules(
    hass: HomeAssistant, rules: Iterable[BlockingRule]
) -> tuple[tuple[CompiledRule, ...], set[str]]:
    """Validate and compile every rule once. Returns the rules and their entities.

    A rule that cannot be compiled is kept with no checkers, which makes it
    always active: a broken rule must not let the robot into a room.
    """
    compiled: list[CompiledRule] = []
    entities: set[str] = set()
    for rule in rules:
        conditions = list(rule.conditions)
        try:
            conditions = await async_validate_conditions_config(hass, conditions)
        except Exception:
            _LOGGER.error(
                "Blocker rule %s has invalid conditions; treating it as active",
                rule.name,
                exc_info=True,
            )
        for condition in conditions:
            try:
                entities.update(async_extract_entities(condition))
            except Exception:
                _LOGGER.warning(
                    "Could not extract entities from blocker rule %s", rule.name,
                    exc_info=True,
                )
        try:
            checks = tuple(
                [await async_from_config(hass, condition) for condition in conditions]
            )
        except Exception:
            _LOGGER.error(
                "Blocker rule %s could not be compiled; treating it as active",
                rule.name,
                exc_info=True,
            )
            checks = ()
        compiled.append(CompiledRule(rule=rule, checks=checks))
    return tuple(compiled), entities


def async_unload_rules(rules: Iterable[CompiledRule]) -> None:
    """Release the checkers held by compiled rules."""
    for rule in rules:
        for check in rule.checks:
            check.async_unload()


def rules_from_legacy_blockers(
    options: Mapping[str, Any],
    rooms: list[Mapping[str, Any]],
    name_for: Callable[[str], str],
) -> list[dict[str, Any]]:
    """Turn 0.1.x entity-id blockers into named rules.

    The same entity used by several rooms of the same kind becomes one rule
    with all of those rooms; a soft and a hard use stay two rules.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    def add(entity_id: Any, kind: str, room: str | None) -> None:
        if not isinstance(entity_id, str) or not entity_id:
            return
        key = (entity_id, kind)
        rule = groups.get(key)
        if rule is None:
            rule = {
                CONF_BLOCKER_ID: uuid4().hex,
                CONF_BLOCKER_NAME: name_for(entity_id),
                CONF_BLOCKER_CONDITIONS: [STATE_CONDITION(entity_id)],
                CONF_BLOCKER_ROOMS: [],
                CONF_BLOCKER_OVERRIDABLE: kind == "soft",
            }
            groups[key] = rule
            order.append(key)
        if room is not None and room not in rule[CONF_BLOCKER_ROOMS]:
            rule[CONF_BLOCKER_ROOMS].append(room)

    for entity_id in options.get(CONF_GLOBAL_BLOCKERS, []) or []:
        add(entity_id, "global", None)

    soft = options.get(CONF_ROOM_BLOCKERS, {}) or {}
    hard = options.get(CONF_ROOM_HARD_BLOCKERS, {}) or {}
    room_ids: list[str] = [str(room[CONF_ROOM_ID]) for room in rooms if CONF_ROOM_ID in room]
    for room_id in list(soft) + list(hard):
        if room_id not in room_ids:
            room_ids.append(str(room_id))
    for room_id in room_ids:
        for entity_id in soft.get(room_id, []) or []:
            add(entity_id, "soft", room_id)
        for entity_id in hard.get(room_id, []) or []:
            add(entity_id, "hard", room_id)

    return [groups[key] for key in order]
