"""The single job-finalising path: judging, attempts, cooldown, notifications."""

from __future__ import annotations

from .helpers import (
    ensure_day,
    judge_rooms,
    names,
    room_done_emits,
    room_name,
)
from .models import (
    Command,
    Config,
    DayStats,
    Emit,
    JobEnd,
    JobOrigin,
    JobResult,
    Notify,
    NotifyAction,
    NotifyKind,
    Snapshot,
    State,
    StopCause,
)


def _skipped_notification(state: State, snap: Snapshot, cfg: Config, room_id: str) -> Notify:
    return Notify(
        kind=NotifyKind.ROOM_SKIPPED,
        title=f"Skipped {room_name(cfg, room_id)}",
        message=(
            f"{room_name(cfg, room_id)} was not cleaned after "
            f"{cfg.max_attempts} attempts today. It is skipped until tomorrow."
        ),
        tag=f"valetudo_smart_clean_skipped_{room_id}",
        actions=(NotifyAction(f"VSC_RETRY:{room_id}", "Try again"),),
    )


def _stopped_notification(
    state: State, snap: Snapshot, cfg: Config, reason: str | None
) -> Notify:
    left = (
        [room for room in state.job.rooms if room not in state.job.done]
        if state.job is not None
        else []
    )
    retry_at = state.cooldown_until
    retry_text = f"Retrying at {retry_at:%H:%M}." if retry_at is not None else ""
    waiting = f" Still to clean: {names(cfg, left)}." if left else ""
    actions: list[NotifyAction] = []
    if left:
        actions.append(
            NotifyAction("VSC_CLEAN_NOW:" + ",".join(left), "Clean anyway")
        )
        actions.extend(
            NotifyAction(f"VSC_SKIP:{room_id}", f"Skip {room_name(cfg, room_id)}")
            for room_id in left[:3]
        )
    return Notify(
        kind=NotifyKind.JOB_STOPPED,
        title="Cleaning stopped",
        message=f"{reason or 'Stopped'}. {retry_text}{waiting}".strip(),
        tag="valetudo_smart_clean_job_stopped",
        actions=tuple(actions),
    )


def _error_notification(state: State, cfg: Config) -> Notify:
    job = state.job
    assert job is not None
    where = f" in {room_name(cfg, job.error_position)}" if job.error_position else ""
    return Notify(
        kind=NotifyKind.JOB_ERROR,
        title="Cleaning error",
        message=f"{job.error or 'Robot error'}{where}.",
        tag="valetudo_smart_clean_job_error",
        level="time-sensitive",
    )


def end_job(
    state: State,
    snap: Snapshot,
    cfg: Config,
    end: JobEnd,
    *,
    reason: str | None = None,
) -> list[Command]:
    job = state.job
    if job is None:
        return []

    ensure_day(state, snap)
    today = snap.now.date()
    commands: list[Command] = []

    newly_done = judge_rooms(
        job,
        state,
        snap,
        cfg,
        error_position=job.error_position if end == JobEnd.ERROR else None,
        record_durations=end == JobEnd.COMPLETED,
    )
    commands.extend(room_done_emits(newly_done, snap.now))

    open_rooms = [room for room in job.rooms if room not in job.done and room not in job.dropped]
    failed: list[str] = []
    if job.origin in (JobOrigin.AUTO, JobOrigin.MANUAL):
        if end == JobEnd.COMPLETED:
            failed = open_rooms
        elif end == JobEnd.ERROR:
            if len(open_rooms) == 1:
                failed = open_rooms
            elif len(open_rooms) > 1:
                state.isolate_on = today
        elif end == JobEnd.INTERRUPTED:
            if job.resume_expired:
                failed = [
                    room
                    for room in job.dispatched
                    if room not in job.done and room not in job.dropped
                ]
            elif job.stop == StopCause.PAUSE_TIMEOUT:
                paused_room = job.last_position
                if (
                    paused_room is not None
                    and paused_room in job.rooms
                    and paused_room not in job.done
                    and paused_room not in job.dropped
                ):
                    failed = [paused_room]

    for room_id in failed:
        room_state = state.rooms[room_id]
        if room_state.attempts_on != today:
            room_state.attempts = 0
            room_state.attempts_on = today
        room_state.attempts += 1
        if room_state.attempts >= cfg.max_attempts:
            room_state.skipped_on = today
            commands.append(
                Emit(
                    "room_skipped",
                    {
                        "room": room_id,
                        "attempts": room_state.attempts,
                    },
                )
            )
            commands.append(_skipped_notification(state, snap, cfg, room_id))

    if end == JobEnd.COMPLETED and job.done:
        state.cooldown_until = None
    else:
        state.cooldown_until = snap.now + cfg.retry_delay

    if end == JobEnd.ERROR:
        commands.append(_error_notification(state, cfg))
    elif end == JobEnd.INTERRUPTED and (
        job.stop in (StopCause.BLOCKED, StopCause.PAUSE_TIMEOUT)
        or job.resume_expired
    ):
        commands.append(_stopped_notification(state, snap, cfg, reason or job.stop_reason))
    elif end == JobEnd.DISPATCH_FAILED:
        commands.append(
            Notify(
                kind=NotifyKind.DISPATCH_FAILED,
                title="Robot did not start",
                message=f"{reason or 'The robot did not start cleaning'}.",
                tag="valetudo_smart_clean_dispatch_failed",
            )
        )

    state.last_result = JobResult(
        job_id=job.job_id,
        origin=job.origin,
        end=end,
        ended_at=snap.now,
        done=tuple(job.done),
        failed=tuple(failed),
        dropped=tuple(job.dropped),
        reason=reason or job.stop_reason,
    )
    state.stats.jobs += 1
    if end == JobEnd.ERROR:
        state.stats.errors += 1
    if end == JobEnd.INTERRUPTED:
        state.stats.stops += 1

    commands.append(
        Emit(
            "job_finished",
            {
                "job_id": job.job_id,
                "origin": job.origin.value,
                "end": end.value,
                "done": ",".join(job.done),
                "failed": ",".join(failed),
            },
        )
    )
    state.job = None
    return commands
