"""JSON-safe persistence for State."""

from __future__ import annotations

from datetime import date, datetime

from .models import (
    Config,
    DayStats,
    Job,
    JobEnd,
    JobOrigin,
    JobPhase,
    JobResult,
    RoomState,
    State,
    StopCause,
    VacuumStatus,
)


def _dt(value: datetime) -> str:
    return value.isoformat()


def _opt_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _opt_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_in(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _date_in(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _room_to_dict(room: RoomState) -> dict:
    return {
        "last_cleaned": _opt_dt(room.last_cleaned),
        "attempts": room.attempts,
        "attempts_on": _opt_date(room.attempts_on),
        "skipped_on": _opt_date(room.skipped_on),
        "durations": [float(value) for value in room.durations],
    }


def _room_from_dict(data: dict) -> RoomState:
    return RoomState(
        last_cleaned=_dt_in(data.get("last_cleaned")),
        attempts=int(data.get("attempts") or 0),
        attempts_on=_date_in(data.get("attempts_on")),
        skipped_on=_date_in(data.get("skipped_on")),
        durations=[float(value) for value in (data.get("durations") or [])],
    )


def _job_to_dict(job: Job) -> dict:
    return {
        "job_id": job.job_id,
        "origin": job.origin.value,
        "rooms": list(job.rooms),
        "dispatched": list(job.dispatched),
        "phase": job.phase.value,
        "started_at": _dt(job.started_at),
        "ignore_blockers": job.ignore_blockers,
        "presence": {room: float(value) for room, value in job.presence.items()},
        "done": list(job.done),
        "dropped": list(job.dropped),
        "last_position": job.last_position,
        "last_seen": _opt_dt(job.last_seen),
        "last_vacuum": job.last_vacuum.value if job.last_vacuum is not None else None,
        "stop": job.stop.value if job.stop is not None else None,
        "stop_reason": job.stop_reason,
        "return_home_sent_at": _opt_dt(job.return_home_sent_at),
        "resume_expired": job.resume_expired,
        "error": job.error,
        "error_position": job.error_position,
    }


def _job_from_dict(data: dict) -> Job:
    last_vacuum = data.get("last_vacuum")
    stop = data.get("stop")
    started_at = _dt_in(data.get("started_at"))
    assert started_at is not None
    return Job(
        job_id=str(data["job_id"]),
        origin=JobOrigin(data["origin"]),
        rooms=tuple(data.get("rooms") or ()),
        dispatched=tuple(data.get("dispatched") or ()),
        phase=JobPhase(data["phase"]),
        started_at=started_at,
        ignore_blockers=bool(data.get("ignore_blockers")),
        presence={
            str(room): float(value)
            for room, value in (data.get("presence") or {}).items()
        },
        done=list(data.get("done") or []),
        dropped=list(data.get("dropped") or []),
        last_position=data.get("last_position"),
        last_seen=_dt_in(data.get("last_seen")),
        last_vacuum=VacuumStatus(last_vacuum) if last_vacuum else None,
        stop=StopCause(stop) if stop else None,
        stop_reason=data.get("stop_reason"),
        return_home_sent_at=_dt_in(data.get("return_home_sent_at")),
        resume_expired=bool(data.get("resume_expired")),
        error=data.get("error"),
        error_position=data.get("error_position"),
    )


def _result_to_dict(result: JobResult) -> dict:
    return {
        "job_id": result.job_id,
        "origin": result.origin.value,
        "end": result.end.value,
        "ended_at": _dt(result.ended_at),
        "done": list(result.done),
        "failed": list(result.failed),
        "dropped": list(result.dropped),
        "reason": result.reason,
    }


def _result_from_dict(data: dict) -> JobResult:
    ended_at = _dt_in(data.get("ended_at"))
    assert ended_at is not None
    return JobResult(
        job_id=str(data["job_id"]),
        origin=JobOrigin(data["origin"]),
        end=JobEnd(data["end"]),
        ended_at=ended_at,
        done=tuple(data.get("done") or ()),
        failed=tuple(data.get("failed") or ()),
        dropped=tuple(data.get("dropped") or ()),
        reason=data.get("reason"),
    )


def state_to_dict(state: State) -> dict:
    return {
        "smart_clean": state.smart_clean,
        "clean_when_occupied": state.clean_when_occupied,
        "room_enabled": dict(state.room_enabled),
        "rooms": {room_id: _room_to_dict(room) for room_id, room in state.rooms.items()},
        "job": _job_to_dict(state.job) if state.job is not None else None,
        "cooldown_until": _opt_dt(state.cooldown_until),
        "isolate_on": _opt_date(state.isolate_on),
        "last_result": (
            _result_to_dict(state.last_result) if state.last_result is not None else None
        ),
        "report_sent_on": _opt_date(state.report_sent_on),
        "stats": {
            "day": _opt_date(state.stats.day),
            "jobs": state.stats.jobs,
            "errors": state.stats.errors,
            "stops": state.stats.stops,
        },
    }


def state_from_dict(data: dict, cfg: Config) -> State:
    rooms_data = data.get("rooms") or {}
    enabled_data = data.get("room_enabled") or {}
    job_data = data.get("job")
    result_data = data.get("last_result")
    stats_data = data.get("stats") or {}
    return State(
        smart_clean=bool(data.get("smart_clean")),
        clean_when_occupied=bool(data.get("clean_when_occupied")),
        room_enabled={
            room.room_id: bool(enabled_data.get(room.room_id, True))
            for room in cfg.rooms
        },
        rooms={
            room.room_id: _room_from_dict(rooms_data.get(room.room_id) or {})
            for room in cfg.rooms
        },
        job=_job_from_dict(job_data) if job_data else None,
        cooldown_until=_dt_in(data.get("cooldown_until")),
        isolate_on=_date_in(data.get("isolate_on")),
        last_result=_result_from_dict(result_data) if result_data else None,
        report_sent_on=_date_in(data.get("report_sent_on")),
        stats=DayStats(
            day=_date_in(stats_data.get("day")),
            jobs=int(stats_data.get("jobs") or 0),
            errors=int(stats_data.get("errors") or 0),
            stops=int(stats_data.get("stops") or 0),
        ),
    )
