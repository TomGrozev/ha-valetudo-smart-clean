"""Small shared rules: windows, thresholds, blockers and room judging."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    Config,
    Emit,
    Job,
    RoomConfig,
    RoomState,
    Snapshot,
    State,
    VacuumStatus,
)


def room_ids(cfg: Config) -> tuple[str, ...]:
    return tuple(room.room_id for room in cfg.rooms)


def room_cfg(cfg: Config, room_id: str) -> RoomConfig | None:
    for room in cfg.rooms:
        if room.room_id == room_id:
            return room
    return None


def room_name(cfg: Config, room_id: str) -> str:
    room = room_cfg(cfg, room_id)
    return room.name if room else room_id


def names(cfg: Config, ids: tuple[str, ...] | list[str]) -> str:
    return ", ".join(room_name(cfg, room_id) for room_id in ids)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def presence_threshold(cfg: Config, room: RoomState) -> float:
    floor = cfg.min_presence.total_seconds()
    if not room.durations:
        return floor
    return max(floor, cfg.min_presence_fraction * median(room.durations))


def attempts_today(room: RoomState, today) -> int:
    return room.attempts if room.attempts_on == today else 0


def is_enabled(state: State, room_id: str) -> bool:
    return state.room_enabled.get(room_id, True)


def is_due(
    state: State,
    room: RoomConfig,
    cfg: Config,
    today,
    window_start: datetime,
) -> bool:
    if not is_enabled(state, room.room_id):
        return False
    room_state = state.rooms.get(room.room_id)
    if room_state is None:
        return False
    if room_state.last_cleaned is not None and room_state.last_cleaned >= window_start:
        return False
    if room_state.skipped_on == today:
        return False
    if attempts_today(room_state, today) >= cfg.max_attempts:
        return False
    return True


def window_start_dt(now: datetime, cfg: Config) -> datetime:
    return datetime.combine(now.date(), cfg.window_start, tzinfo=now.tzinfo)


def window_end_dt(now: datetime, cfg: Config) -> datetime:
    return datetime.combine(now.date(), cfg.window_end, tzinfo=now.tzinfo)


def in_window(now: datetime, cfg: Config) -> bool:
    if now.weekday() not in cfg.weekdays:
        return False
    return window_start_dt(now, cfg) <= now < window_end_dt(now, cfg)


def next_window_start(now: datetime, cfg: Config) -> datetime | None:
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in cfg.weekdays:
            continue
        candidate = datetime.combine(day, cfg.window_start, tzinfo=now.tzinfo)
        if candidate > now:
            return candidate
    return None


def global_blocker(cfg: Config, snap: Snapshot) -> tuple[str, str] | None:
    for entity in cfg.global_blockers:
        if entity in snap.active_blockers:
            return entity, snap.active_blockers[entity]
    return None


def room_blocker(
    cfg: Config, snap: Snapshot, room_id: str, clean_when_occupied: bool = False
) -> tuple[str, str] | None:
    room = room_cfg(cfg, room_id)
    if room is None:
        return None
    for entity in room.hard_blockers:
        if entity in snap.active_blockers:
            return entity, snap.active_blockers[entity]
    if clean_when_occupied:
        return None
    for entity in room.blockers:
        if entity in snap.active_blockers:
            return entity, snap.active_blockers[entity]
    return None


def remaining(job: Job) -> list[str]:
    return [room for room in job.rooms if room not in job.done and room not in job.dropped]


def judged_rooms(job: Job, cfg: Config) -> tuple[str, ...]:
    if job.rooms:
        return job.rooms
    return tuple(room.room_id for room in cfg.rooms if job.presence.get(room.room_id, 0) > 0)


def judging_position(snap: Snapshot) -> str | None:
    """Room the robot is still busy in; a docked/returning/offline robot is done there."""
    if snap.vacuum in (
        VacuumStatus.DOCKED,
        VacuumStatus.RETURNING,
        VacuumStatus.UNAVAILABLE,
    ):
        return None
    return snap.position


def judge_rooms(
    job: Job,
    state: State,
    snap: Snapshot,
    cfg: Config,
    *,
    error_position: str | None = None,
    record_durations: bool = False,
) -> list[str]:
    """Mark rooms done; returns the ids newly marked, in job order."""
    newly_done: list[str] = []
    current = judging_position(snap)
    for room_id in judged_rooms(job, cfg):
        if room_id in job.done or room_id in job.dropped:
            continue
        if error_position is not None and room_id == error_position:
            continue
        # A room the robot is still in can still be being cleaned.
        if room_id == current:
            continue
        room_state = state.rooms.get(room_id)
        if room_state is None:
            continue
        presence = job.presence.get(room_id, 0.0)
        if presence < presence_threshold(cfg, room_state):
            continue
        job.done.append(room_id)
        room_state.last_cleaned = snap.now
        if record_durations:
            room_state.durations = (room_state.durations + [presence])[-5:]
        newly_done.append(room_id)
    return newly_done


def room_done_emits(room_ids_: list[str], now: datetime) -> list[Emit]:
    return [
        Emit("room_done", {"room": room_id, "cleaned_at": now.isoformat()})
        for room_id in room_ids_
    ]


def ensure_day(state: State, snap: Snapshot) -> None:
    today = snap.now.date()
    if state.stats.day != today:
        state.stats.day = today
        state.stats.jobs = 0
        state.stats.errors = 0
        state.stats.stops = 0


def is_cleaning(status: VacuumStatus) -> bool:
    return status == VacuumStatus.CLEANING
