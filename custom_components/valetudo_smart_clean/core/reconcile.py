"""One level-triggered tick: observe, advance, plan, fan, report."""

from __future__ import annotations

from copy import deepcopy

from . import planner, tracker
from .helpers import (
    ensure_day,
    global_blocker,
    is_due,
    is_enabled,
    names,
    remaining,
    room_blocker,
    window_end_dt,
    window_start_dt,
)
from .models import (
    Command,
    Config,
    Emit,
    Job,
    JobOrigin,
    JobPhase,
    Notify,
    NotifyKind,
    Result,
    SetFan,
    Snapshot,
    State,
    VacuumStatus,
)


def _maybe_external(state: State, snap: Snapshot) -> list[Command]:
    if state.job is not None:
        return []
    flag = (snap.flag or "").lower()
    external = snap.vacuum == VacuumStatus.CLEANING or (
        snap.vacuum == VacuumStatus.RETURNING and flag in {"segment", "zone"}
    )
    if not external:
        return []
    state.job = Job(
        job_id=f"{snap.now.isoformat()}:{JobOrigin.EXTERNAL.value}",
        origin=JobOrigin.EXTERNAL,
        rooms=(),
        dispatched=(),
        phase=JobPhase.RUNNING,
        started_at=snap.now,
        last_position=snap.position,
        last_seen=snap.now,
        last_vacuum=snap.vacuum,
    )
    return [
        Emit(
            "job_started",
            {
                "job_id": state.job.job_id,
                "origin": JobOrigin.EXTERNAL.value,
                "rooms": "",
            },
        )
    ]


def _fan(state: State, snap: Snapshot, cfg: Config, *, ended: bool) -> list[Command]:
    if ended:
        if cfg.quiet_travel and snap.fan != cfg.clean_fan:
            return [SetFan(cfg.clean_fan)]
        return []
    job = state.job
    if (
        not cfg.quiet_travel
        or job is None
        or job.phase != JobPhase.RUNNING
        or snap.vacuum != VacuumStatus.CLEANING
    ):
        return []
    if job.origin == JobOrigin.EXTERNAL:
        in_job_room = snap.position is not None
    else:
        in_job_room = snap.position in remaining(job)
    desired = cfg.clean_fan if in_job_room else cfg.travel_fan
    if desired == snap.fan:
        return []
    return [SetFan(desired)]


def _report(state: State, snap: Snapshot, cfg: Config) -> list[Command]:
    if not cfg.daily_report:
        return []
    today = snap.now.date()
    if snap.now < window_end_dt(snap.now, cfg):
        return []
    if state.report_sent_on == today:
        return []
    if not (state.smart_clean or state.stats.jobs > 0):
        return []

    start = window_start_dt(snap.now, cfg)
    enabled = [room for room in cfg.rooms if is_enabled(state, room.room_id)]
    cleaned = [
        room
        for room in enabled
        if state.rooms[room.room_id].last_cleaned is not None
        and state.rooms[room.room_id].last_cleaned >= start
    ]
    skipped = [
        room for room in enabled if state.rooms[room.room_id].skipped_on == today
    ]
    gate_reason = None
    blocker = global_blocker(cfg, snap)
    if blocker is not None:
        gate_reason = blocker[1]
    waiting: list[str] = []
    for room in enabled:
        room_state = state.rooms[room.room_id]
        if not is_due(state, room, cfg, today, start):
            continue
        reason = room_blocker(cfg, snap, room.room_id, state.clean_when_occupied)
        text = reason[1] if reason else gate_reason
        waiting.append(f"{room.name} ({text})" if text else room.name)

    message = f"{len(cleaned)} of {len(enabled)} rooms cleaned today"
    if skipped:
        message += f". Skipped: {names(cfg, [room.room_id for room in skipped])}"
    if waiting:
        message += f". Waiting: {', '.join(waiting)}"
    if state.stats.errors:
        message += f". Errors: {state.stats.errors}"
    if state.stats.stops:
        message += f". Stops: {state.stats.stops}"
    state.report_sent_on = today
    return [
        Notify(
            kind=NotifyKind.DAILY_REPORT,
            title="Daily cleaning report",
            message=message,
            tag=f"valetudo_smart_clean_report_{today.isoformat()}",
            level="passive",
        )
    ]


def reconcile(state: State, snap: Snapshot, cfg: Config) -> Result:
    new_state = deepcopy(state)
    ensure_day(new_state, snap)

    commands: list[Command] = []
    ended = False
    if new_state.job is not None:
        tracker.credit_presence(new_state, snap, cfg)
        advanced, ended = tracker.advance(new_state, snap, cfg)
        commands.extend(advanced)

    if new_state.job is None and not ended:
        commands.extend(_maybe_external(new_state, snap))
    if new_state.job is None:
        commands.extend(planner.plan(new_state, snap, cfg))

    commands.extend(_fan(new_state, snap, cfg, ended=ended))
    commands.extend(_report(new_state, snap, cfg))
    return Result(new_state, tuple(commands))
