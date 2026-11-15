"""Job lifecycle: recharge, errors, pause timeout, dispatch timeout, external jobs."""

from datetime import timedelta

from core.models import (
    CleanRooms,
    JobEnd,
    JobOrigin,
    JobPhase,
    NotifyKind,
    ReturnHome,
    StopCause,
    VacuumStatus,
)

from .harness import Sim, at, clean_commands, make_cfg, make_state, notifies


def test_mid_job_recharge_does_not_end_the_job():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(20)

    sim.step(0, vacuum=VacuumStatus.DOCKED, flag="resumable")
    assert sim.state.job is not None
    sim.step(300, flag="resumable")
    assert sim.state.job is not None
    assert sim.state.job.phase == JobPhase.RUNNING
    assert sim.state.last_result is None
    assert clean_commands(sim.commands) == []
    assert not any(isinstance(command, ReturnHome) for command in sim.commands)

    sim.step(0, vacuum=VacuumStatus.CLEANING, flag=None, position="kitchen")
    sim.step(20)
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED)
    sim.step(60)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen",)


def test_error_with_several_open_rooms_consumes_no_attempts_and_isolates():
    cfg = make_cfg(("kitchen", "toilet", "study"), retry_delay=timedelta(minutes=60))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    ended = sim.step(0, vacuum=VacuumStatus.ERROR, error="Stuck", position="kitchen")
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.ERROR
    assert sim.state.last_result.failed == ()
    assert sim.state.isolate_on == sim.now.date()
    assert sim.state.cooldown_until is not None
    assert all(room.attempts == 0 for room in sim.state.rooms.values())
    errors = notifies(ended, NotifyKind.JOB_ERROR)
    assert len(errors) == 1
    assert errors[0].level == "time-sensitive"
    assert "Stuck" in errors[0].message

    sim.step(0, vacuum=VacuumStatus.DOCKED, error=None)
    dispatch = sim.step(3600)
    assert clean_commands(dispatch) == [CleanRooms(("kitchen",))]
    assert sim.state.job is not None


def test_pause_timeout_returns_home_and_only_current_room_consumes_attempt():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    sim.step(0, vacuum=VacuumStatus.PAUSED, position="kitchen")
    assert sim.state.job is not None
    assert sim.state.job.stop is None

    paused = sim.step(301)
    assert any(isinstance(command, ReturnHome) for command in paused)
    assert sim.state.job is not None
    assert sim.state.job.phase == JobPhase.RETURNING
    assert sim.state.job.stop == StopCause.PAUSE_TIMEOUT
    assert sim.state.rooms["kitchen"].attempts == 0

    sim.step(0, vacuum=VacuumStatus.DOCKED)
    finished = sim.step(60)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.failed == ("kitchen",)
    assert sim.state.rooms["kitchen"].attempts == 1
    assert sim.state.rooms["toilet"].attempts == 0
    assert sim.state.cooldown_until is not None
    assert len(notifies(finished, NotifyKind.JOB_STOPPED)) == 1


def test_dispatch_timeout_fails_without_consuming_attempts():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    assert clean_commands(sim.step(0)) == [CleanRooms(("kitchen",))]
    ended = sim.step(181)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.DISPATCH_FAILED
    assert sim.state.rooms["kitchen"].attempts == 0
    assert sim.state.cooldown_until is not None
    assert len(notifies(ended, NotifyKind.DISPATCH_FAILED)) == 1


def test_external_job_stamps_rooms_without_consuming_attempts():
    cfg = make_cfg(("kitchen", "toilet"))
    state = make_state(cfg, at(10, 0), smart_clean=False)
    sim = Sim(
        cfg,
        state,
        at(10, 0),
        vacuum=VacuumStatus.CLEANING,
        position="kitchen",
    )
    started = sim.step(0)
    assert sim.state.job is not None
    assert sim.state.job.origin == JobOrigin.EXTERNAL
    assert len(notifies(started)) == 0

    sim.step(60)
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED)
    sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.origin == JobOrigin.EXTERNAL
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen",)
    assert sim.state.rooms["kitchen"].last_cleaned == sim.now
    assert sim.state.rooms["kitchen"].attempts == 0
    assert sim.state.rooms["toilet"].attempts == 0
