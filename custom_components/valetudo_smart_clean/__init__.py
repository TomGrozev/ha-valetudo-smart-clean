"""Valetudo Smart Clean: a restart-safe room-cleaning planner for Valetudo robots.

The integration is a thin Home Assistant shell around the pure ``core``
package: it turns entity states into a :class:`Snapshot`, calls the core, saves
the returned :class:`State` and runs the returned commands in order. Every
decision is recomputed from current state, so a missed event, a restart or a
dropped service call self-heals on the next tick.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from functools import partial
from typing import Any, Final

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import Event, HomeAssistant, ServiceCall, State as HaState, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_IGNORE_BLOCKERS,
    ATTR_REASON,
    ATTR_ROOM,
    ATTR_ROOMS,
    ATTR_WHEN,
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
    CONF_ROOM_BLOCKERS,
    CONF_ROOM_HARD_BLOCKERS,
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
    DEFAULT_MOP_WEEKDAYS,
    DEFAULT_PAUSE_TIMEOUT,
    DEFAULT_POSITION_ATTRIBUTE,
    DEFAULT_RESUME_TIMEOUT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TRAVEL_FAN,
    DEFAULT_WEEKDAYS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    EVENT_COMMAND_FAILED,
    EVENT_NOTIFICATION_ACTION,
    PLATFORMS,
    SERVICE_CLEAN_ROOMS,
    SERVICE_MARK_CLEANED,
    SERVICE_RETRY_TODAY,
    SERVICE_SKIP_TODAY,
    SERVICE_STOP,
    SERVICES,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
    TICK_INTERVAL,
)
from .core import (
    apply_intent,
    describe,
    new_state,
    parse_action,
    reconcile,
    state_from_dict,
    state_to_dict,
)
from .core.models import (
    CleanNow,
    CleanRooms,
    Command,
    Config,
    Emit,
    Intent,
    MarkCleaned,
    Notify,
    Result,
    RetryToday,
    ReturnHome,
    RoomConfig,
    SetFan,
    SetMode,
    SetRoomEnabled,
    SetSmartClean,
    SkipToday,
    Snapshot,
    State,
    StopJob,
    VacuumStatus,
)
from .values import parse_time, parse_weekdays

_LOGGER = logging.getLogger(__name__)

VACUUM_STATUSES: Final[dict[str, VacuumStatus]] = {
    "cleaning": VacuumStatus.CLEANING,
    "docked": VacuumStatus.DOCKED,
    "idle": VacuumStatus.IDLE,
    "paused": VacuumStatus.PAUSED,
    "returning": VacuumStatus.RETURNING,
    "error": VacuumStatus.ERROR,
}

NO_ERROR_VALUES: Final = frozenset({"no error", "none", ""})
"""Valetudo reports the absence of an error as text, never as an empty field."""

SAVE_DELAY: Final = 1
"""Seconds to coalesce state writes; the tick cadence makes this invisible."""


# --- configuration ---------------------------------------------------------


def _seconds(options: dict[str, Any], key: str, default: int) -> timedelta:
    """Read a duration option stored as whole seconds."""
    return timedelta(seconds=int(options.get(key, default)))


def _optional_text(options: dict[str, Any], key: str) -> str | None:
    value = options.get(key)
    return value if isinstance(value, str) and value else None


def build_config(entry: ConfigEntry) -> Config:
    """Build the immutable core config from the entry data and options."""
    data = entry.data
    options = entry.options
    positions: dict[str, list[str]] = options.get(CONF_ROOM_POSITIONS, {})
    blockers: dict[str, list[str]] = options.get(CONF_ROOM_BLOCKERS, {})
    hard_blockers: dict[str, list[str]] = options.get(CONF_ROOM_HARD_BLOCKERS, {})

    rooms = tuple(
        RoomConfig(
            room_id=room[CONF_ROOM_ID],
            name=room.get(CONF_ROOM_NAME, room[CONF_ROOM_ID]),
            position_values=tuple(
                positions.get(room[CONF_ROOM_ID], room.get(CONF_POSITION_VALUES, []))
            ),
            blockers=tuple(blockers.get(room[CONF_ROOM_ID], [])),
            hard_blockers=tuple(hard_blockers.get(room[CONF_ROOM_ID], [])),
        )
        for room in data.get(CONF_ROOMS, [])
    )

    return Config(
        rooms=rooms,
        global_blockers=tuple(options.get(CONF_GLOBAL_BLOCKERS, [])),
        window_start=parse_time(options.get(CONF_WINDOW_START), DEFAULT_WINDOW_START),
        window_end=parse_time(options.get(CONF_WINDOW_END), DEFAULT_WINDOW_END),
        weekdays=parse_weekdays(options.get(CONF_WEEKDAYS), DEFAULT_WEEKDAYS),
        min_battery=int(options.get(CONF_MIN_BATTERY, DEFAULT_MIN_BATTERY)),
        dock_dwell=_seconds(options, CONF_DOCK_DWELL, DEFAULT_DOCK_DWELL),
        end_settle=_seconds(options, CONF_END_SETTLE, DEFAULT_END_SETTLE),
        dispatch_timeout=_seconds(
            options, CONF_DISPATCH_TIMEOUT, DEFAULT_DISPATCH_TIMEOUT
        ),
        pause_timeout=_seconds(options, CONF_PAUSE_TIMEOUT, DEFAULT_PAUSE_TIMEOUT),
        resume_timeout=_seconds(options, CONF_RESUME_TIMEOUT, DEFAULT_RESUME_TIMEOUT),
        retry_delay=_seconds(options, CONF_RETRY_DELAY, DEFAULT_RETRY_DELAY),
        max_attempts=int(options.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)),
        min_presence=_seconds(options, CONF_MIN_PRESENCE, DEFAULT_MIN_PRESENCE),
        min_presence_fraction=float(
            options.get(CONF_MIN_PRESENCE_FRACTION, DEFAULT_MIN_PRESENCE_FRACTION)
        ),
        max_presence_gap=_seconds(
            options, CONF_MAX_PRESENCE_GAP, DEFAULT_MAX_PRESENCE_GAP
        ),
        vacuum_mode=_optional_text(options, CONF_VACUUM_MODE),
        mop_mode=_optional_text(options, CONF_MOP_MODE),
        mop_weekdays=parse_weekdays(options.get(CONF_MOP_WEEKDAYS), DEFAULT_MOP_WEEKDAYS),
        quiet_travel=bool(options.get(CONF_QUIET_TRAVEL, False)),
        travel_fan=str(options.get(CONF_TRAVEL_FAN, DEFAULT_TRAVEL_FAN)),
        clean_fan=str(options.get(CONF_CLEAN_FAN, DEFAULT_CLEAN_FAN)),
        daily_report=bool(options.get(CONF_DAILY_REPORT, DEFAULT_DAILY_REPORT)),
    )


# --- snapshot --------------------------------------------------------------


def _ha_state(hass: HomeAssistant, entity_id: str | None) -> HaState | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in {"unavailable", "unknown"}:
        return None
    return state


def _attribute_value(state: HaState, path: str) -> Any:
    """Read a (possibly nested) attribute path like ``vacuum_position.in_room``."""
    value: Any = state.attributes
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _position(hass: HomeAssistant, data: dict[str, Any], cfg: Config) -> str | None:
    entity_id = data.get(CONF_POSITION_ENTITY)
    state = _ha_state(hass, entity_id)
    if state is None:
        return None
    path = data.get(CONF_POSITION_ATTRIBUTE) or DEFAULT_POSITION_ATTRIBUTE
    raw = _attribute_value(state, path)
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    for room in cfg.rooms:
        if value in {candidate.strip().lower() for candidate in room.position_values}:
            return room.room_id
    return None


def _active_blockers(hass: HomeAssistant, cfg: Config) -> dict[str, str]:
    """Return blocker entity id -> reason for every blocker that is exactly on."""
    blockers: dict[str, str] = {}
    entity_ids = list(cfg.global_blockers)
    for room in cfg.rooms:
        entity_ids.extend(room.blockers)
        entity_ids.extend(room.hard_blockers)
    for entity_id in dict.fromkeys(entity_ids):
        state = hass.states.get(entity_id)
        if state is None or state.state != "on":
            continue
        reason = state.name or state.entity_id
        blockers[entity_id] = reason
    return blockers


def build_snapshot(hass: HomeAssistant, cfg: Config, data: dict[str, Any]) -> Snapshot:
    """Build the core snapshot from the watched entity states."""
    now = dt_util.now()
    vacuum_state = _ha_state(hass, data.get(CONF_VACUUM))
    vacuum = (
        VACUUM_STATUSES.get(vacuum_state.state, VacuumStatus.UNAVAILABLE)
        if vacuum_state is not None
        else VacuumStatus.UNAVAILABLE
    )
    vacuum_since = vacuum_state.last_changed if vacuum_state is not None else now
    if vacuum_since.tzinfo is None:
        vacuum_since = vacuum_since.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

    flag_state = _ha_state(hass, data.get(CONF_STATUS_FLAG))
    flag = flag_state.state.strip().lower() if flag_state is not None else None

    error_state = _ha_state(hass, data.get(CONF_ERROR))
    error: str | None = None
    if error_state is not None and error_state.state.strip().lower() not in NO_ERROR_VALUES:
        error = error_state.state

    dock_state = _ha_state(hass, data.get(CONF_DOCK_STATUS))
    dock_status = dock_state.state.strip().lower() if dock_state is not None else None

    consumables: list[str] = data.get(CONF_DOCK_CONSUMABLES, [])
    dock_ok = all(
        (state := hass.states.get(entity_id)) is not None and state.state == "ok"
        for entity_id in consumables
    )

    battery_state = _ha_state(hass, data.get(CONF_BATTERY))
    battery: int | None = None
    if battery_state is not None:
        try:
            battery = int(float(battery_state.state))
        except (TypeError, ValueError):
            battery = None

    fan_state = _ha_state(hass, data.get(CONF_FAN))
    mode_state = _ha_state(hass, data.get(CONF_MODE))

    return Snapshot(
        now=now,
        vacuum=vacuum,
        vacuum_since=vacuum_since,
        flag=flag,
        error=error,
        dock_status=dock_status,
        dock_ok=dock_ok,
        battery=battery,
        position=_position(hass, data, cfg),
        active_blockers=_active_blockers(hass, cfg),
        fan=fan_state.state if fan_state is not None else None,
        mode=mode_state.state if mode_state is not None else None,
    )


# --- runtime ---------------------------------------------------------------


class SmartCleanRuntime:
    """One running planner for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.data: dict[str, Any] = dict(entry.data)
        self.config = build_config(entry)
        self.state: State = new_state(self.config)
        self.status = describe(
            self.state, build_snapshot(hass, self.config, self.data), self.config
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._lock = asyncio.Lock()
        self._stopped = False
        self._unsubs: list[Callable[[], None]] = []
        self._saved: dict[str, Any] = state_to_dict(self.state)

    @property
    def signal(self) -> str:
        return f"{SIGNAL_UPDATE}_{self.entry.entry_id}"

    # -- lifecycle --------------------------------------------------------

    async def async_load(self) -> None:
        """Restore the persisted planner state, if any."""
        stored = await self._store.async_load()
        if stored:
            try:
                self.state = state_from_dict(stored, self.config)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning(
                    "Stored smart clean state for %s is unreadable; starting fresh",
                    self.entry.entry_id,
                    exc_info=True,
                )
        self._saved = state_to_dict(self.state)
        self.status = describe(
            self.state, build_snapshot(self.hass, self.config, self.data), self.config
        )

    @callback
    def async_start(self) -> None:
        """Install the watchers that drive the reconcile loop."""
        entity_ids = self._watched_entity_ids()
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, entity_ids, self._handle_state_change
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._handle_tick, TICK_INTERVAL)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_NOTIFICATION_ACTION, self._handle_notification_action
            )
        )

    async def async_stop(self) -> None:
        """Remove every listener and flush the latest state once."""
        async with self._lock:
            self._stopped = True
            while self._unsubs:
                self._unsubs.pop()()
            payload = state_to_dict(self.state)
            await self._store.async_save(payload)
            self._saved = payload

    def _watched_entity_ids(self) -> list[str]:
        ids: list[str] = [
            self.data.get(key)
            for key in (
                CONF_VACUUM,
                CONF_STATUS_FLAG,
                CONF_ERROR,
                CONF_DOCK_STATUS,
                CONF_BATTERY,
                CONF_FAN,
                CONF_MODE,
                CONF_POSITION_ENTITY,
            )
        ]
        ids.extend(self.data.get(CONF_DOCK_CONSUMABLES, []))
        ids.extend(self.config.global_blockers)
        for room in self.config.rooms:
            ids.extend(room.blockers)
            ids.extend(room.hard_blockers)
        return [entity_id for entity_id in dict.fromkeys(ids) if entity_id]

    # -- driving the core -------------------------------------------------

    @callback
    def _handle_state_change(self, event: Event) -> None:
        self.hass.async_create_task(self.async_reconcile())

    @callback
    def _handle_tick(self, now: datetime | None = None) -> None:
        self.hass.async_create_task(self.async_reconcile())

    @callback
    def _handle_notification_action(self, event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        intent = parse_action(action)
        if intent is None:
            return
        self.hass.async_create_task(self.async_intent(intent))

    async def async_reconcile(self) -> None:
        """Recompute the plan from current state and run any commands."""
        if self._stopped:
            return
        async with self._lock:
            if self._stopped:
                return
            await self._reconcile_locked()

    async def async_intent(self, intent: Intent) -> None:
        """Apply a user intent, then re-plan from the new state."""
        if self._stopped:
            return
        async with self._lock:
            if self._stopped:
                return
            snap = self._snapshot()
            result = apply_intent(self.state, intent, snap, self.config)
            await self._apply(result, snap)
            await self._reconcile_locked()

    async def _reconcile_locked(self) -> None:
        snap = self._snapshot()
        result = reconcile(self.state, snap, self.config)
        await self._apply(result, snap)

    def _snapshot(self) -> Snapshot:
        return build_snapshot(self.hass, self.config, self.data)

    async def _apply(self, result: Result, snap: Snapshot) -> None:
        self.state = result.state
        self.status = describe(self.state, snap, self.config)
        self._save()
        for command in result.commands:
            await self._execute(command)
        async_dispatcher_send(self.hass, self.signal)

    def _save(self) -> None:
        payload = state_to_dict(self.state)
        if payload == self._saved:
            return
        self._saved = payload
        self._store.async_delay_save(lambda: payload, SAVE_DELAY)

    # -- commands ---------------------------------------------------------

    async def _execute(self, command: Command) -> None:
        try:
            await self._run(command)
        except Exception as err:  # noqa: BLE001 - one bad command must not stop the rest
            _LOGGER.warning(
                "Valetudo Smart Clean could not run %s: %s",
                type(command).__name__,
                err,
            )
            self.hass.bus.async_fire(
                DOMAIN,
                {
                    "type": EVENT_COMMAND_FAILED,
                    "entry_id": self.entry.entry_id,
                    "command": type(command).__name__,
                    "error": str(err),
                },
            )

    async def _run(self, command: Command) -> None:
        """Run one core command; the HA layer never retries (the core owns retries)."""
        if isinstance(command, CleanRooms):
            await self.hass.services.async_call(
                "vacuum",
                "clean_area",
                {
                    "entity_id": self.data[CONF_VACUUM],
                    "cleaning_area_id": list(command.rooms),
                },
                blocking=True,
            )
        elif isinstance(command, ReturnHome):
            await self.hass.services.async_call(
                "vacuum",
                "return_to_base",
                {"entity_id": self.data[CONF_VACUUM]},
                blocking=True,
            )
        elif isinstance(command, SetFan):
            await self._select_option(CONF_FAN, command.speed)
        elif isinstance(command, SetMode):
            await self._select_option(CONF_MODE, command.mode)
        elif isinstance(command, Notify):
            await self._notify(command)
        elif isinstance(command, Emit):
            self.hass.bus.async_fire(
                DOMAIN, {"type": command.kind, **command.data, "entry_id": self.entry.entry_id}
            )

    async def _select_option(self, entity_key: str, option: str) -> None:
        entity_id = self.data.get(entity_key)
        if not entity_id:
            _LOGGER.debug("No %s entity configured; skipping select_option", entity_key)
            return
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )

    async def _notify(self, command: Notify) -> None:
        script = self.data.get(CONF_NOTIFY_SCRIPT)
        if not script:
            _LOGGER.debug("No notification script configured; dropping %s", command.kind)
            return
        await self.hass.services.async_call(
            "script",
            "turn_on",
            {
                "entity_id": script,
                "variables": {
                    "title": command.title,
                    "message": command.message,
                    "tag": command.tag,
                    "level": command.level,
                    "actions": [
                        {"action": action.action, "title": action.title}
                        for action in command.actions
                    ],
                },
            },
            blocking=True,
        )


# --- device ----------------------------------------------------------------


@callback
def device_info(runtime: SmartCleanRuntime) -> DeviceInfo:
    """One device per config entry, named after the robot."""
    vacuum_state = runtime.hass.states.get(runtime.data.get(CONF_VACUUM, ""))
    robot_name = vacuum_state.name if vacuum_state is not None else runtime.entry.title
    return DeviceInfo(
        identifiers={(DOMAIN, runtime.entry.entry_id)},
        name=f"{robot_name} Smart Clean",
        manufacturer="Valetudo Smart Clean",
        model="Room cleaning planner",
    )


# --- services --------------------------------------------------------------


def _runtime_for(hass: HomeAssistant, call: ServiceCall) -> SmartCleanRuntime:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError(
                f"Config entry {entry_id} is not a running Valetudo Smart Clean entry"
            )
        return entry.runtime_data
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if len(entries) == 1:
        return entries[0].runtime_data
    if not entries:
        raise ServiceValidationError("Valetudo Smart Clean is not set up")
    raise ServiceValidationError(
        "More than one robot is configured; pass config_entry_id"
    )


def _known_room(runtime: SmartCleanRuntime, room: str) -> str:
    if room not in {configured.room_id for configured in runtime.config.rooms}:
        raise ServiceValidationError(f"Unknown room {room}")
    return room


def _parse_when(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            return parsed
    return dt_util.now()


async def _async_clean_rooms(hass: HomeAssistant, call: ServiceCall) -> None:
    runtime = _runtime_for(hass, call)
    rooms = call.data.get(ATTR_ROOMS, [])
    if not rooms:
        raise ServiceValidationError("At least one room is required")
    for room in rooms:
        _known_room(runtime, room)
    await runtime.async_intent(
        CleanNow(tuple(rooms), call.data.get(ATTR_IGNORE_BLOCKERS, True))
    )


async def _async_skip_today(hass: HomeAssistant, call: ServiceCall) -> None:
    runtime = _runtime_for(hass, call)
    room = call.data.get(ATTR_ROOM)
    if not room:
        raise ServiceValidationError("A room is required")
    await runtime.async_intent(SkipToday(_known_room(runtime, room)))


async def _async_retry_today(hass: HomeAssistant, call: ServiceCall) -> None:
    runtime = _runtime_for(hass, call)
    room = call.data.get(ATTR_ROOM)
    await runtime.async_intent(
        RetryToday(_known_room(runtime, room) if room else None)
    )


async def _async_mark_cleaned(hass: HomeAssistant, call: ServiceCall) -> None:
    runtime = _runtime_for(hass, call)
    room = call.data.get(ATTR_ROOM)
    if not room:
        raise ServiceValidationError("A room is required")
    await runtime.async_intent(
        MarkCleaned(_known_room(runtime, room), _parse_when(call.data.get(ATTR_WHEN)))
    )


async def _async_stop(hass: HomeAssistant, call: ServiceCall) -> None:
    runtime = _runtime_for(hass, call)
    await runtime.async_intent(StopJob(call.data.get(ATTR_REASON) or "Stopped by user"))


SERVICE_HANDLERS: Final = {
    SERVICE_CLEAN_ROOMS: _async_clean_rooms,
    SERVICE_SKIP_TODAY: _async_skip_today,
    SERVICE_RETRY_TODAY: _async_retry_today,
    SERVICE_MARK_CLEANED: _async_mark_cleaned,
    SERVICE_STOP: _async_stop,
}


SERVICE_SCHEMAS: Final = {
    SERVICE_CLEAN_ROOMS: vol.Schema(
        {
            vol.Required(ATTR_ROOMS): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(ATTR_IGNORE_BLOCKERS, default=True): cv.boolean,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        }
    ),
    SERVICE_SKIP_TODAY: vol.Schema(
        {
            vol.Required(ATTR_ROOM): cv.string,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        }
    ),
    SERVICE_RETRY_TODAY: vol.Schema(
        {
            vol.Optional(ATTR_ROOM): cv.string,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        }
    ),
    SERVICE_MARK_CLEANED: vol.Schema(
        {
            vol.Required(ATTR_ROOM): cv.string,
            vol.Optional(ATTR_WHEN): cv.datetime,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        }
    ),
    SERVICE_STOP: vol.Schema(
        {
            vol.Optional(ATTR_REASON): cv.string,
            vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        }
    ),
}


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    for service, handler in SERVICE_HANDLERS.items():
        if hass.services.has_service(DOMAIN, service):
            continue
        hass.services.async_register(
            DOMAIN,
            service,
            partial(handler, hass),
            schema=SERVICE_SCHEMAS[service],
        )


# --- entry points ----------------------------------------------------------


type SmartCleanConfigEntry = ConfigEntry[SmartCleanRuntime]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the integration's services once for every entry."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartCleanConfigEntry) -> bool:
    """Set up one planning entry."""
    runtime = SmartCleanRuntime(hass, entry)
    await runtime.async_load()
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, list(PLATFORMS))
    runtime.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await runtime.async_reconcile()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartCleanConfigEntry) -> bool:
    """Unload one planning entry and remove its listeners."""
    if not await hass.config_entries.async_unload_platforms(entry, list(PLATFORMS)):
        return False
    await entry.runtime_data.async_stop()
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: SmartCleanConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
