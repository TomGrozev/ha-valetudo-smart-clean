"""Types shared by the pure core and the Home Assistant layer.

Nothing under core/ imports homeassistant. The Home Assistant layer turns entity
states into a Snapshot, calls the core, saves the returned State and runs the
returned Commands in order. All cleaning decisions live in the core, so they can
be tested as plain Python against recorded event sequences.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

# --- configuration -----------------------------------------------------------


@dataclass(frozen=True)
class RoomConfig:
    room_id: str
    """Home Assistant area id, passed to vacuum.clean_area."""
    name: str
    position_values: tuple[str, ...] = ()
    """Raw values of the position source that mean the robot is in this room."""
    blockers: tuple[str, ...] = ()
    """Entity ids; the room is blocked while any of them is on. clean_when_occupied bypasses these."""
    hard_blockers: tuple[str, ...] = ()
    """Entity ids that block the room even with clean_when_occupied (e.g. a sleeping child).
    Only a job with ignore_blockers bypasses them."""


@dataclass(frozen=True)
class Config:
    rooms: tuple[RoomConfig, ...]
    """Order is the automatic cleaning order."""
    global_blockers: tuple[str, ...] = ()
    """Entity ids; no automatic cleaning while any of them is on. Only a job with
    ignore_blockers bypasses them; clean_when_occupied does not."""
    window_start: time = time(9, 0)
    window_end: time = time(17, 0)
    weekdays: frozenset[int] = frozenset(range(7))
    """date.weekday() values on which automatic cleaning may start; 0 is Monday."""
    min_battery: int = 30
    dock_dwell: timedelta = timedelta(minutes=2)
    """How long the robot must sit docked before an automatic job starts."""
    end_settle: timedelta = timedelta(seconds=60)
    """How long docked or idle must hold before a job counts as ended."""
    dispatch_timeout: timedelta = timedelta(minutes=3)
    pause_timeout: timedelta = timedelta(minutes=5)
    resume_timeout: timedelta = timedelta(hours=3)
    """How long a running job may sit docked with flag resumable (mid-job recharge) before it ends."""
    retry_delay: timedelta = timedelta(minutes=60)
    max_attempts: int = 2
    """Failed automatic attempts per room per day before the room is skipped."""
    min_presence: timedelta = timedelta(seconds=30)
    min_presence_fraction: float = 0.4
    """A room needs max(min_presence, fraction * its median clean time) of presence."""
    max_presence_gap: timedelta = timedelta(seconds=90)
    """Longest gap between observations credited as presence (covers HA downtime)."""
    vacuum_mode: str | None = None
    """Mode for automatic jobs on non-mop days; None leaves the mode alone."""
    mop_mode: str | None = None
    mop_weekdays: frozenset[int] = frozenset()
    quiet_travel: bool = False
    """Low fan while crossing rooms that are not in the job."""
    travel_fan: str = "low"
    clean_fan: str = "medium"
    daily_report: bool = True


# --- observations ------------------------------------------------------------


class VacuumStatus(StrEnum):
    CLEANING = "cleaning"
    DOCKED = "docked"
    IDLE = "idle"
    PAUSED = "paused"
    RETURNING = "returning"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    """Also used for unknown."""


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    """Timezone-aware, in Home Assistant's local time zone."""
    vacuum: VacuumStatus
    vacuum_since: datetime
    """When the vacuum entered its current status."""
    flag: str | None
    """Valetudo status flag, lower case: none, resumable, segment, zone, emptying, washing..."""
    error: str | None
    """Valetudo error text; None when there is no error."""
    dock_status: str | None
    """Valetudo dock status: idle, drying, emptying, cleaning, pause, error; None without a dock entity."""
    dock_ok: bool
    """Every configured dock consumable reports ok."""
    battery: int | None
    position: str | None
    """room_id the position source reports, in every vacuum state; None when unknown or outside
    every room. The core decides when a position counts (the HA layer never masks it)."""
    active_blockers: Mapping[str, str]
    """Blocker entity id -> reason text, for blockers that are on now."""
    fan: str | None
    mode: str | None


# --- persisted state ---------------------------------------------------------


class JobOrigin(StrEnum):
    AUTO = "auto"
    """Started by the planner."""
    MANUAL = "manual"
    """Started through the clean_rooms service or a notification button."""
    EXTERNAL = "external"
    """Started outside the integration: map card, Valetudo UI, robot button."""


class JobPhase(StrEnum):
    DISPATCHED = "dispatched"
    """Command sent; waiting for the robot to start cleaning."""
    RUNNING = "running"
    RETURNING = "returning"
    """The integration sent the robot home."""


class StopCause(StrEnum):
    BLOCKED = "blocked"
    PAUSE_TIMEOUT = "pause_timeout"
    USER = "user"


class JobEnd(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    DISPATCH_FAILED = "dispatch_failed"


@dataclass
class RoomState:
    last_cleaned: datetime | None = None
    attempts: int = 0
    attempts_on: date | None = None
    """attempts count only on this date."""
    skipped_on: date | None = None
    """Skipped for this date, after max_attempts or a user skip."""
    durations: list[float] = field(default_factory=list)
    """Presence seconds of recent completed cleans, newest last, at most 5."""


@dataclass
class Job:
    job_id: str
    origin: JobOrigin
    rooms: tuple[str, ...]
    """Requested rooms; empty for EXTERNAL, where any room can count."""
    dispatched: tuple[str, ...]
    """Rooms in the last command sent to the robot."""
    phase: JobPhase
    started_at: datetime
    ignore_blockers: bool = False
    presence: dict[str, float] = field(default_factory=dict)
    """Cumulative seconds per room while the robot was cleaning."""
    done: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    """Removed mid-job because they became blocked; no attempt is consumed."""
    last_position: str | None = None
    last_seen: datetime | None = None
    last_vacuum: VacuumStatus | None = None
    """Vacuum status at last_seen; presence is credited only for time spent cleaning."""
    stop: StopCause | None = None
    stop_reason: str | None = None
    return_home_sent_at: datetime | None = None
    """When ReturnHome was last emitted; RETURNING retries it once per dispatch_timeout."""
    resume_expired: bool = False
    """Docked with flag resumable past resume_timeout; attempts are charged on the end."""
    error: str | None = None
    error_position: str | None = None


@dataclass(frozen=True)
class JobResult:
    job_id: str
    origin: JobOrigin
    end: JobEnd
    ended_at: datetime
    done: tuple[str, ...]
    failed: tuple[str, ...]
    """Rooms that consumed an attempt."""
    dropped: tuple[str, ...]
    reason: str | None


@dataclass
class DayStats:
    day: date | None = None
    jobs: int = 0
    errors: int = 0
    stops: int = 0


@dataclass
class State:
    smart_clean: bool = False
    clean_when_occupied: bool = False
    room_enabled: dict[str, bool] = field(default_factory=dict)
    rooms: dict[str, RoomState] = field(default_factory=dict)
    job: Job | None = None
    cooldown_until: datetime | None = None
    isolate_on: date | None = None
    """One room per automatic job on this date, after an error left several rooms unclear."""
    last_result: JobResult | None = None
    report_sent_on: date | None = None
    stats: DayStats = field(default_factory=DayStats)


# --- outputs -----------------------------------------------------------------


@dataclass(frozen=True)
class CleanRooms:
    rooms: tuple[str, ...]


@dataclass(frozen=True)
class ReturnHome:
    reason: str


@dataclass(frozen=True)
class SetFan:
    speed: str


@dataclass(frozen=True)
class SetMode:
    mode: str


class NotifyKind(StrEnum):
    ROOM_SKIPPED = "room_skipped"
    JOB_STOPPED = "job_stopped"
    JOB_ERROR = "job_error"
    DISPATCH_FAILED = "dispatch_failed"
    DAILY_REPORT = "daily_report"


@dataclass(frozen=True)
class NotifyAction:
    action: str
    """Id the phone sends back; parse_action() turns it into an Intent."""
    title: str


@dataclass(frozen=True)
class Notify:
    kind: NotifyKind
    title: str
    message: str
    tag: str
    level: str = "active"
    """active, time-sensitive or passive."""
    actions: tuple[NotifyAction, ...] = ()


@dataclass(frozen=True)
class Emit:
    """Fired on the Home Assistant bus as event valetudo_smart_clean with {"type": kind, **data}."""

    kind: str
    data: Mapping[str, str | int | float | None]


Command = CleanRooms | ReturnHome | SetFan | SetMode | Notify | Emit


@dataclass(frozen=True)
class Result:
    state: State
    commands: tuple[Command, ...]


# --- user intents ------------------------------------------------------------


@dataclass(frozen=True)
class CleanNow:
    rooms: tuple[str, ...]
    ignore_blockers: bool = True


@dataclass(frozen=True)
class SkipToday:
    room: str


@dataclass(frozen=True)
class RetryToday:
    room: str | None = None
    """None clears every room's skip and attempts for today."""


@dataclass(frozen=True)
class MarkCleaned:
    room: str
    when: datetime


@dataclass(frozen=True)
class SetSmartClean:
    on: bool


@dataclass(frozen=True)
class SetCleanWhenOccupied:
    on: bool


@dataclass(frozen=True)
class SetRoomEnabled:
    room: str
    on: bool


@dataclass(frozen=True)
class StopJob:
    reason: str = "Stopped by user"


Intent = (
    CleanNow
    | SkipToday
    | RetryToday
    | MarkCleaned
    | SetSmartClean
    | SetCleanWhenOccupied
    | SetRoomEnabled
    | StopJob
)


# --- views for entities ------------------------------------------------------


class RoomStatus(StrEnum):
    CLEANING = "cleaning"
    READY = "ready"
    """Due and nothing blocks it."""
    WAITING = "waiting"
    """Due but blocked, or a global gate holds automatic cleaning."""
    FRESH = "fresh"
    """Cleaned since today's window start."""
    SKIPPED = "skipped"
    EXCLUDED = "excluded"
    """Automatic cleaning is off for this room."""


@dataclass(frozen=True)
class RoomView:
    room_id: str
    name: str
    status: RoomStatus
    reason: str | None
    last_cleaned: datetime | None
    estimate_s: float | None
    attempts_today: int


@dataclass(frozen=True)
class StatusView:
    sentence: str
    """One line for the dashboard."""
    phase: str
    """off, idle, waiting, cooldown, dispatched, cleaning, returning."""
    failed_gates: tuple[str, ...]
    job_rooms: tuple[str, ...]
    remaining: tuple[str, ...]
    next_attempt_at: datetime | None
    rooms: tuple[RoomView, ...]
