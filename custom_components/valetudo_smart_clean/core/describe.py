"""StatusView: what the dashboard entities show, recomputed from state."""

from __future__ import annotations

from datetime import datetime

from .helpers import (
    attempts_today,
    dock_trip,
    global_blocker,
    is_due,
    is_enabled,
    median,
    next_window_start,
    remaining,
    room_blocker,
    room_name,
    window_start_dt,
)
from .models import (
    Config,
    JobPhase,
    RoomStatus,
    RoomView,
    Snapshot,
    State,
    StatusView,
)
from .planner import due_rooms, failed_gates


def _join_then(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", then " + parts[-1]


def _gate_reason(
    gate: str, state: State, snap: Snapshot, cfg: Config, today
) -> str:
    if gate == "smart_clean":
        return "Smart clean is off"
    if gate == "cooldown" and state.cooldown_until is not None:
        return f"Retrying at {state.cooldown_until:%H:%M}"
    if gate == "window":
        start = next_window_start(snap.now, cfg)
        return f"Outside the cleaning window (next {start:%H:%M})" if start else "Outside the cleaning window"
    if gate == "vacuum":
        return "Robot is not docked"
    if gate == "dock_dwell":
        return "Waiting for the robot to settle"
    if gate == "battery":
        return "Battery is low"
    if gate == "error":
        return f"Robot error: {snap.error}" if snap.error else "Robot has an error"
    if gate == "dock":
        return "Dock needs attention"
    if gate == "flag":
        return "Robot is busy"
    if gate == "blocked":
        blocker = global_blocker(cfg, snap)
        return blocker[1] if blocker is not None else "A blocker is on"
    if gate == "nothing_due":
        return "All rooms are clean for today"
    return gate


def _window_text(when: datetime, today) -> str:
    return "today" if when.date() == today else "tomorrow"


def describe(state: State, snap: Snapshot, cfg: Config) -> StatusView:
    today = snap.now.date()
    start = window_start_dt(snap.now, cfg)
    gates = failed_gates(state, snap, cfg)
    job = state.job
    job_rooms = job.rooms if job is not None else ()
    left = tuple(remaining(job)) if job is not None else ()

    next_attempt_at: datetime | None = None
    if state.cooldown_until is not None and state.cooldown_until > snap.now:
        next_attempt_at = state.cooldown_until
    elif "window" in gates:
        next_attempt_at = next_window_start(snap.now, cfg)

    if job is not None:
        if job.phase == JobPhase.DISPATCHED:
            phase = "dispatched"
            sentence = "Starting " + _join_then([room_name(cfg, room) for room in left])
        elif job.phase == JobPhase.RETURNING:
            phase = "returning"
            sentence = "Returning home"
        elif dock_trip(job, snap):
            phase = "cleaning"
            dock = (snap.dock_status or "").lower()
            where = f" ({dock})" if dock else ""
            sentence = (
                f"Docked mid-clean{where}; will carry on with "
                + _join_then([room_name(cfg, room) for room in left])
            )
        else:
            phase = "cleaning"
            sentence = (
                "Cleaning " + _join_then([room_name(cfg, room) for room in left])
                if left
                else "Finishing up"
            )
    elif not state.smart_clean:
        phase = "off"
        sentence = "Smart clean is off"
    elif next_attempt_at is not None and state.cooldown_until == next_attempt_at:
        phase = "cooldown"
        sentence = f"Retrying at {next_attempt_at:%H:%M}"
    elif "nothing_due" in gates:
        due_ids = due_rooms(state, snap, cfg)
        room_reason = None
        for room_id in due_ids:
            blocker = room_blocker(cfg, snap, room_id, state.clean_when_occupied)
            if blocker is not None:
                room_reason = blocker[1]
                break
        if due_ids and room_reason is not None:
            phase = "waiting"
            sentence = f"Waiting: {room_reason}"
        else:
            phase = "idle"
            sentence = "All rooms are clean for today"
    elif "window" in gates:
        phase = "waiting"
        due_names = [
            room.name
            for room in cfg.rooms
            if is_due(state, room, cfg, today, start)
        ]
        if due_names and next_attempt_at is not None:
            sentence = (
                f"Next: {due_names[0]} at {next_attempt_at:%H:%M} "
                f"{_window_text(next_attempt_at, today)}"
            )
        else:
            sentence = "All rooms are clean for today"
    elif gates:
        phase = "waiting"
        sentence = "Waiting: " + _gate_reason(gates[0], state, snap, cfg, today)
    else:
        phase = "idle"
        sentence = "Idle"

    room_views: list[RoomView] = []
    for room in cfg.rooms:
        room_id = room.room_id
        room_state = state.rooms[room_id]
        attempts = attempts_today(room_state, today)
        estimate = median(room_state.durations) if room_state.durations else None
        reason: str | None = None

        if not is_enabled(state, room_id):
            status = RoomStatus.EXCLUDED
            reason = "Automatic cleaning is off for this room"
        elif (
            job is not None
            and room_id in job.rooms
            and room_id not in job.done
            and room_id not in job.dropped
        ):
            status = RoomStatus.CLEANING
        elif room_state.skipped_on == today or attempts >= cfg.max_attempts:
            status = RoomStatus.SKIPPED
            reason = "Skipped today"
        elif room_state.last_cleaned is not None and room_state.last_cleaned >= start:
            status = RoomStatus.FRESH
        else:
            due = is_due(state, room, cfg, today, start)
            blocker = room_blocker(
                cfg, snap, room_id, state.clean_when_occupied
            )
            if blocker is not None:
                status = RoomStatus.WAITING
                reason = blocker[1]
            elif due and gates:
                status = RoomStatus.WAITING
                reason = _gate_reason(gates[0], state, snap, cfg, today)
            elif due:
                status = RoomStatus.READY
            else:
                status = RoomStatus.WAITING

        room_views.append(
            RoomView(
                room_id=room_id,
                name=room.name,
                status=status,
                reason=reason,
                last_cleaned=room_state.last_cleaned,
                estimate_s=estimate,
                attempts_today=attempts,
            )
        )

    return StatusView(
        sentence=sentence,
        phase=phase,
        failed_gates=gates,
        job_rooms=tuple(job_rooms),
        remaining=left,
        next_attempt_at=next_attempt_at,
        rooms=tuple(room_views),
    )
