"""Presence accounting, room judging during a job, and job lifecycle transitions."""

from __future__ import annotations

from .finalise import end_job
from .helpers import (
    global_blocker,
    judge_rooms,
    remaining,
    room_blocker,
    room_done_emits,
)
from .models import (
    CleanRooms,
    Command,
    Config,
    Job,
    JobEnd,
    JobOrigin,
    JobPhase,
    ReturnHome,
    Snapshot,
    State,
    StopCause,
    VacuumStatus,
)


def _begin_return(
    job: Job,
    snap: Snapshot,
    reason: str,
    stop: StopCause,
    commands: list[Command],
) -> None:
    job.phase = JobPhase.RETURNING
    job.stop = stop
    job.stop_reason = reason
    job.return_home_sent_at = snap.now
    commands.append(ReturnHome(reason))


def credit_presence(state: State, snap: Snapshot, cfg: Config) -> None:
    job = state.job
    if job is None:
        return
    if (
        job.last_seen is not None
        and job.last_position is not None
        and job.last_position in state.rooms
        and job.last_vacuum == VacuumStatus.CLEANING
    ):
        delta = (snap.now - job.last_seen).total_seconds()
        if delta > 0:
            cap = cfg.max_presence_gap.total_seconds()
            job.presence[job.last_position] = (
                job.presence.get(job.last_position, 0.0) + min(delta, cap)
            )
    # Keep the last room seen while the robot is busy: completing or pausing
    # later must still know where it was. Docked/returning/offline positions
    # are ignored so a dock inside a room cannot block that room at job end.
    if snap.vacuum in (
        VacuumStatus.CLEANING,
        VacuumStatus.PAUSED,
        VacuumStatus.IDLE,
    ):
        job.last_position = snap.position
    job.last_vacuum = snap.vacuum
    job.last_seen = snap.now


def judge_now(state: State, snap: Snapshot, cfg: Config) -> list[Command]:
    job = state.job
    if job is None:
        return []
    newly_done = judge_rooms(job, state, snap, cfg)
    return room_done_emits(newly_done, snap.now)


def drop_rooms(
    state: State,
    snap: Snapshot,
    cfg: Config,
    room_ids: list[str],
    reason: str,
) -> list[Command]:
    """Drop rooms that became blocked (or were skipped) and re-dispatch the rest."""
    job = state.job
    if job is None:
        return []
    commands: list[Command] = judge_now(state, snap, cfg)
    drop = [
        room_id
        for room_id in room_ids
        if room_id in job.rooms
        and room_id not in job.done
        and room_id not in job.dropped
    ]
    if not drop:
        return commands
    job.dropped.extend(drop)
    left = remaining(job)
    if left:
        if tuple(left) != job.dispatched:
            job.dispatched = tuple(left)
            commands.append(CleanRooms(tuple(left)))
    else:
        job.phase = JobPhase.RETURNING
        job.stop = StopCause.BLOCKED
        job.stop_reason = reason
        job.return_home_sent_at = snap.now
        commands.append(ReturnHome(reason))
    return commands


def _remaining_blocked(state: State, snap: Snapshot, cfg: Config) -> list[str]:
    job = state.job
    assert job is not None
    return [
        room_id
        for room_id in remaining(job)
        if room_blocker(cfg, snap, room_id, state.clean_when_occupied) is not None
    ]


def advance(
    state: State, snap: Snapshot, cfg: Config
) -> tuple[list[Command], bool]:
    """Advance the current job. Returns commands and whether it ended this tick."""
    job = state.job
    if job is None:
        return [], False

    # An error is terminal whatever phase the job reached.
    if snap.vacuum == VacuumStatus.ERROR:
        job.error = snap.error or "Robot error"
        job.error_position = snap.position
        return end_job(state, snap, cfg, JobEnd.ERROR, reason=job.error), True

    if job.phase == JobPhase.DISPATCHED:
        if snap.vacuum == VacuumStatus.CLEANING:
            job.phase = JobPhase.RUNNING
        elif snap.vacuum in (VacuumStatus.DOCKED, VacuumStatus.IDLE):
            # Time observed unavailable must not count: a reconnect is not a
            # failed dispatch. Only a docked/idle observation ages this timeout.
            observed_since = max(job.started_at, snap.vacuum_since)
            if snap.now - observed_since >= cfg.dispatch_timeout:
                return (
                    end_job(
                        state,
                        snap,
                        cfg,
                        JobEnd.DISPATCH_FAILED,
                        reason="Robot did not start cleaning",
                    ),
                    True,
                )

    commands: list[Command] = []
    managed = (
        job.origin in (JobOrigin.AUTO, JobOrigin.MANUAL)
        and not job.ignore_blockers
    )
    if job.phase == JobPhase.RUNNING and managed:
        blocker = global_blocker(cfg, snap)
        if blocker is not None:
            _begin_return(job, snap, blocker[1], StopCause.BLOCKED, commands)
            return commands, False

        blocked = _remaining_blocked(state, snap, cfg)
        if blocked:
            reason = room_blocker(cfg, snap, blocked[0])
            assert reason is not None
            commands.extend(drop_rooms(state, snap, cfg, blocked, reason[1]))
            return commands, False

    if (
        job.phase == JobPhase.RUNNING
        and snap.vacuum in (VacuumStatus.PAUSED, VacuumStatus.IDLE)
        and snap.now - snap.vacuum_since >= cfg.pause_timeout
    ):
        _begin_return(job, snap, "Paused too long", StopCause.PAUSE_TIMEOUT, commands)
        return commands, False

    resumable = (snap.flag or "").lower() == "resumable"
    settled = snap.now - snap.vacuum_since >= cfg.end_settle

    if job.phase == JobPhase.RUNNING:
        if (
            snap.vacuum == VacuumStatus.DOCKED
            and resumable
            and snap.now - snap.vacuum_since >= cfg.resume_timeout
        ):
            job.resume_expired = True
            job.stop_reason = "did not resume after recharging"
            return (
                end_job(
                    state,
                    snap,
                    cfg,
                    JobEnd.INTERRUPTED,
                    reason=job.stop_reason,
                ),
                True,
            )
        if (
            not resumable
            and snap.vacuum in (VacuumStatus.DOCKED, VacuumStatus.IDLE)
            and settled
        ):
            end = (
                JobEnd.INTERRUPTED
                if job.stop is not None or job.resume_expired
                else JobEnd.COMPLETED
            )
            return end_job(state, snap, cfg, end, reason=job.stop_reason), True

    if job.phase == JobPhase.RETURNING:
        if snap.vacuum == VacuumStatus.CLEANING:
            last_sent = job.return_home_sent_at
            if last_sent is None or snap.now - last_sent >= cfg.dispatch_timeout:
                reason = job.stop_reason or "Returning home"
                job.return_home_sent_at = snap.now
                commands.append(ReturnHome(reason))
        elif (
            snap.vacuum in (VacuumStatus.DOCKED, VacuumStatus.IDLE)
            and settled
        ):
            # Our own ReturnHome means the job is over; whatever the flag says,
            # a settled dock is the end.
            end = (
                JobEnd.INTERRUPTED
                if job.stop is not None or job.resume_expired
                else JobEnd.COMPLETED
            )
            return end_job(state, snap, cfg, end, reason=job.stop_reason), True
    return commands, False
