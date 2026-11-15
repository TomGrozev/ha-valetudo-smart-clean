"""Constants for the Valetudo Smart Clean integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from .core.models import Config as _CoreConfig

_CORE_DEFAULTS: Final = _CoreConfig(rooms=())

DOMAIN: Final = "valetudo_smart_clean"

PLATFORMS: Final = ("switch", "sensor")

STORAGE_VERSION: Final = 1
CONFIG_ENTRY_VERSION: Final = 2
"""v2 replaced entity-id blocker lists with named condition rules."""
CONFIG_ENTRY_MINOR_VERSION: Final = 1
TICK_INTERVAL: Final = timedelta(seconds=15)

EVENT_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"
"""Event fired by the Home Assistant companion apps when a notification button is tapped."""

# --- config entry data: the Valetudo entities the snapshot is built from ----

CONF_VACUUM: Final = "vacuum"
CONF_STATUS_FLAG: Final = "status_flag"
CONF_ERROR: Final = "error"
CONF_DOCK_STATUS: Final = "dock_status"
CONF_BATTERY: Final = "battery"
CONF_DOCK_CONSUMABLES: Final = "dock_consumables"
CONF_FAN: Final = "fan"
CONF_MODE: Final = "mode"
CONF_POSITION_ENTITY: Final = "position_entity"
CONF_POSITION_ATTRIBUTE: Final = "position_attribute"
CONF_NOTIFY_SCRIPT: Final = "notify_script"
CONF_MAP_SEGMENTS: Final = "map_segments"

CONF_ROOMS: Final = "rooms"
CONF_ROOM_ID: Final = "room_id"
CONF_ROOM_NAME: Final = "name"
CONF_POSITION_VALUES: Final = "position_values"

DEFAULT_POSITION_ATTRIBUTE: Final = "vacuum_position.in_room"
"""Valetudo map camera attribute carrying the segment name the robot is in."""

# --- options: schedule and behaviour ---------------------------------------

CONF_BLOCKER_RULES: Final = "blocker_rules"
CONF_BLOCKER_ID: Final = "id"
CONF_BLOCKER_NAME: Final = "name"
CONF_BLOCKER_CONDITIONS: Final = "conditions"
CONF_BLOCKER_ROOMS: Final = "rooms"
CONF_BLOCKER_OVERRIDABLE: Final = "overridable"
CONF_ROOM_POSITIONS: Final = "room_positions"

# Legacy keys, read only by async_migrate_entry to turn 0.1.x entity-id
# blockers into named rules.
CONF_GLOBAL_BLOCKERS: Final = "global_blockers"
CONF_ROOM_BLOCKERS: Final = "room_blockers"
CONF_ROOM_HARD_BLOCKERS: Final = "room_hard_blockers"

CONF_WINDOW_START: Final = "window_start"
CONF_WINDOW_END: Final = "window_end"
CONF_WEEKDAYS: Final = "weekdays"
CONF_MOP_WEEKDAYS: Final = "mop_weekdays"
CONF_VACUUM_MODE: Final = "vacuum_mode"
CONF_MOP_MODE: Final = "mop_mode"

CONF_MIN_BATTERY: Final = "min_battery"
CONF_DOCK_DWELL: Final = "dock_dwell"
CONF_END_SETTLE: Final = "end_settle"
CONF_DISPATCH_TIMEOUT: Final = "dispatch_timeout"
CONF_PAUSE_TIMEOUT: Final = "pause_timeout"
CONF_RESUME_TIMEOUT: Final = "resume_timeout"
CONF_RETRY_DELAY: Final = "retry_delay"
CONF_MAX_ATTEMPTS: Final = "max_attempts"
CONF_MIN_PRESENCE: Final = "min_presence"
CONF_MIN_PRESENCE_FRACTION: Final = "min_presence_fraction"
CONF_MAX_PRESENCE_GAP: Final = "max_presence_gap"
CONF_QUIET_TRAVEL: Final = "quiet_travel"
CONF_TRAVEL_FAN: Final = "travel_fan"
CONF_CLEAN_FAN: Final = "clean_fan"
CONF_DAILY_REPORT: Final = "daily_report"

DEFAULT_WINDOW_START: Final = _CORE_DEFAULTS.window_start
DEFAULT_WINDOW_END: Final = _CORE_DEFAULTS.window_end
DEFAULT_MIN_BATTERY: Final = _CORE_DEFAULTS.min_battery
DEFAULT_DOCK_DWELL: Final = int(_CORE_DEFAULTS.dock_dwell.total_seconds())
DEFAULT_END_SETTLE: Final = int(_CORE_DEFAULTS.end_settle.total_seconds())
DEFAULT_DISPATCH_TIMEOUT: Final = int(
    _CORE_DEFAULTS.dispatch_timeout.total_seconds()
)
DEFAULT_PAUSE_TIMEOUT: Final = int(_CORE_DEFAULTS.pause_timeout.total_seconds())
DEFAULT_RESUME_TIMEOUT: Final = int(_CORE_DEFAULTS.resume_timeout.total_seconds())
DEFAULT_RETRY_DELAY: Final = int(_CORE_DEFAULTS.retry_delay.total_seconds())
DEFAULT_MAX_ATTEMPTS: Final = _CORE_DEFAULTS.max_attempts
DEFAULT_MIN_PRESENCE: Final = int(_CORE_DEFAULTS.min_presence.total_seconds())
DEFAULT_MIN_PRESENCE_FRACTION: Final = _CORE_DEFAULTS.min_presence_fraction
DEFAULT_MAX_PRESENCE_GAP: Final = int(
    _CORE_DEFAULTS.max_presence_gap.total_seconds()
)
DEFAULT_TRAVEL_FAN: Final = _CORE_DEFAULTS.travel_fan
DEFAULT_CLEAN_FAN: Final = _CORE_DEFAULTS.clean_fan
DEFAULT_DAILY_REPORT: Final = _CORE_DEFAULTS.daily_report
DEFAULT_WEEKDAYS: Final = frozenset(_CORE_DEFAULTS.weekdays)
DEFAULT_MOP_WEEKDAYS: Final = frozenset(_CORE_DEFAULTS.mop_weekdays)

WEEKDAY_NAMES: Final = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# --- services --------------------------------------------------------------

SERVICE_CLEAN_ROOMS: Final = "clean_rooms"
SERVICE_SKIP_TODAY: Final = "skip_today"
SERVICE_RETRY_TODAY: Final = "retry_today"
SERVICE_MARK_CLEANED: Final = "mark_cleaned"
SERVICE_STOP: Final = "stop"

ATTR_ROOMS: Final = "rooms"
ATTR_ROOM: Final = "room"
ATTR_IGNORE_BLOCKERS: Final = "ignore_blockers"
ATTR_WHEN: Final = "when"
ATTR_REASON: Final = "reason"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

SERVICES: Final = (
    SERVICE_CLEAN_ROOMS,
    SERVICE_SKIP_TODAY,
    SERVICE_RETRY_TODAY,
    SERVICE_MARK_CLEANED,
    SERVICE_STOP,
)

# --- dispatch helpers ------------------------------------------------------

SIGNAL_UPDATE: Final = f"{DOMAIN}_update"
EVENT_COMMAND_FAILED: Final = "command_failed"
