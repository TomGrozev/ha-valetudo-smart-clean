"""User intents: immediate state changes and the notification action parser."""

from __future__ import annotations

from copy import deepcopy

from .finalise import end_job
from .helpers import ensure_day, room_ids
from .models import (
    CleanNow,
    CleanRooms,
    Command,
    Config,
    Emit,
    Intent,
    Job,
    JobEnd,
    JobOrigin,
    JobPhase,
    MarkCleaned,
    Result,
    RetryToday,
    ReturnHome,
    SetCleanWhenOccupied,
    SetRoomEnabled,
    SetSmartClean,
    SkipToday,
    Snapshot,
    State,
    StopCause,
    StopJob,
    VacuumStatus,
)
from .tracker import drop_rooms


def parse_action(action_id: str) -> Intent | None:
    if not action_id:
        return None
    if action_id == "VSC_RETRY":
        return RetryToday(None)
    if action_id.startswith("VSC_RETRY:"):
        room = action_id.split(":", 1)[1].strip()
        return RetryToday(room) if room else None
    if action_id.startswith("VSC_SKIP:"):
        room = action_id.split(":", 1)[1].strip()
        return SkipToday(room) if room else None
    if action_id.startswith("VSC_CLEAN_NOW:"):
        raw = action_id.split(":", 1)[1]
        rooms = tuple(part.strip() for part in raw.split(",") if part.strip())
        return CleanNow(rooms, True) if rooms else None
    return None


def _clean_now(state: State, snap: Snapshot, cfg: Config, intent: CleanNow) -> list[Command]:
    known = set(room_ids(cfg))
    rooms = tuple(intent.rooms)
    if not rooms or any(room not in known for room in rooms):
        return []
    if snap.vacuum in (VacuumStatus.ERROR, VacuumStatus.UNAVAILABLE):
        return []

    commands: list[Command] = []
    if state.job is not None:
        state.job.stop = StopCause.USER
        state.job.stop_reason = "Replaced by a manual clean"
        commands.extend(
            end_job(
                state,
                snap,
                cfg,
                JobEnd.INTERRUPTED,
                reason="Replaced by a manual clean",
            )
        )
    state.cooldown_until = None

    state.job = Job(
        job_id=f"{snap.now.isoformat()}:{JobOrigin.MANUAL.value}",
        origin=JobOrigin.MANUAL,
        rooms=rooms,
        dispatched=rooms,
        phase=JobPhase.DISPATCHED,
        started_at=snap.now,
        ignore_blockers=intent.ignore_blockers,
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
                "origin": JobOrigin.MANUAL.value,
                "rooms": ",".join(rooms),
            },
        )
    )
    return commands


def apply_intent(state: State, intent: Intent, snap: Snapshot, cfg: Config) -> Result:
    new_state = deepcopy(state)
    ensure_day(new_state, snap)
    today = snap.now.date()
    commands: list[Command] = []

    if isinstance(intent, CleanNow):
        commands = _clean_now(new_state, snap, cfg, intent)

    elif isinstance(intent, SkipToday):
        room = new_state.rooms.get(intent.room)
        if room is not None:
            room.skipped_on = today
            job = new_state.job
            if (
                job is not None
                and job.phase == JobPhase.RUNNING
                and intent.room in job.rooms
                and intent.room not in job.done
                and intent.room not in job.dropped
            ):
                commands = drop_rooms(
                    new_state,
                    snap,
                    cfg,
                    [intent.room],
                    "Skipped by user",
                )

    elif isinstance(intent, RetryToday):
        targets = room_ids(cfg) if intent.room is None else (intent.room,)
        for room_id in targets:
            room = new_state.rooms.get(room_id)
            if room is None:
                continue
            room.skipped_on = None
            room.attempts = 0
            room.attempts_on = None
        new_state.cooldown_until = None
        new_state.isolate_on = None

    elif isinstance(intent, MarkCleaned):
        room = new_state.rooms.get(intent.room)
        if room is not None:
            room.last_cleaned = intent.when

    elif isinstance(intent, SetSmartClean):
        new_state.smart_clean = intent.on

    elif isinstance(intent, SetCleanWhenOccupied):
        new_state.clean_when_occupied = intent.on

    elif isinstance(intent, SetRoomEnabled):
        if intent.room in new_state.rooms:
            new_state.room_enabled[intent.room] = intent.on

    elif isinstance(intent, StopJob):
        job = new_state.job
        if job is not None and job.phase != JobPhase.RETURNING:
            job.phase = JobPhase.RETURNING
            job.stop = StopCause.USER
            job.stop_reason = intent.reason
            job.return_home_sent_at = snap.now
            commands = [ReturnHome(intent.reason)]

    return Result(new_state, tuple(commands))
