"""Automatic dispatch: gates, due rooms and the first job of the day."""

from __future__ import annotations

from .helpers import (
    global_blocker,
    in_window,
    is_due,
    room_blocker,
    window_start_dt,
)
from .models import (
    CleanRooms,
    Command,
    Config,
    Emit,
    Job,
    JobOrigin,
    JobPhase,
    SetMode,
    Snapshot,
    State,
    VacuumStatus,
)

FLAGS_BUSY = frozenset({"emptying", "washing", "paused", "resumable"})


def due_rooms(state: State, snap: Snapshot, cfg: Config) -> list[str]:
    today = snap.now.date()
    start = window_start_dt(snap.now, cfg)
    return [
        room.room_id
        for room in cfg.rooms
        if is_due(state, room, cfg, today, start)
    ]


def eligible_rooms(state: State, snap: Snapshot, cfg: Config) -> list[str]:
    return [
        room_id
        for room_id in due_rooms(state, snap, cfg)
        if room_blocker(cfg, snap, room_id, state.clean_when_occupied) is None
    ]


def failed_gates(state: State, snap: Snapshot, cfg: Config) -> tuple[str, ...]:
    today = snap.now.date()
    gates: list[str] = []
    if not state.smart_clean:
        gates.append("smart_clean")
    if state.cooldown_until is not None and snap.now < state.cooldown_until:
        gates.append("cooldown")
    if not in_window(snap.now, cfg):
        gates.append("window")
    if snap.vacuum != VacuumStatus.DOCKED:
        gates.append("vacuum")
    if snap.now - snap.vacuum_since < cfg.dock_dwell:
        gates.append("dock_dwell")
    if snap.battery is None or snap.battery < cfg.min_battery:
        gates.append("battery")
    if snap.error:
        gates.append("error")
    dock_status = (snap.dock_status or "").lower()
    if not snap.dock_ok or dock_status not in {"", "idle", "drying"}:
        gates.append("dock")
    if (snap.flag or "").lower() in FLAGS_BUSY:
        gates.append("flag")
    if global_blocker(cfg, snap) is not None:
        gates.append("blocked")
    if not eligible_rooms(state, snap, cfg):
        gates.append("nothing_due")
    return tuple(gates)


def _job_id(now, origin: JobOrigin) -> str:
    return f"{now.isoformat()}:{origin.value}"


def plan(state: State, snap: Snapshot, cfg: Config) -> list[Command]:
    if failed_gates(state, snap, cfg):
        return []
    eligible = eligible_rooms(state, snap, cfg)
    if state.isolate_on == snap.now.date():
        eligible = eligible[:1]
    if not eligible:
        return []

    today = snap.now.date()
    mode = cfg.mop_mode if today.weekday() in cfg.mop_weekdays else cfg.vacuum_mode
    commands: list[Command] = []
    if mode is not None and mode != snap.mode:
        commands.append(SetMode(mode))

    rooms = tuple(eligible)
    state.job = Job(
        job_id=_job_id(snap.now, JobOrigin.AUTO),
        origin=JobOrigin.AUTO,
        rooms=rooms,
        dispatched=rooms,
        phase=JobPhase.DISPATCHED,
        started_at=snap.now,
        last_position=snap.position,
        last_seen=snap.now,
        last_vacuum=snap.vacuum,
    )
    commands.append(CleanRooms(rooms))
    commands.append(
        Emit(
            "job_started",
            {
                "job_id": state.job.job_id,
                "origin": JobOrigin.AUTO.value,
                "rooms": ",".join(rooms),
            },
        )
    )
    return commands
