"""Mid-job dock trips: a bin-empty stop is not the end of the job.

Replays the 2026-11-22 and 2026-11-23 traces: the robot goes home mid-clean,
Valetudo clears the resumable flag on arrival, and the job must stay open until
the robot either carries on by itself or the remaining rooms are sent again.
"""

from datetime import timedelta

from core.models import CleanRooms, Emit, JobEnd, JobPhase, VacuumStatus

from .harness import Sim, at, clean_commands, make_cfg, make_state


def started_job(rooms=("kitchen", "toilet", "study"), **overrides):
    cfg = make_cfg(rooms, **overrides)
    sim = Sim(
        cfg,
        make_state(cfg, at(12, 23)),
        at(12, 23),
        vacuum=VacuumStatus.DOCKED,
        vacuum_since=at(12, 20),
    )
    assert clean_commands(sim.step(0)) == [CleanRooms(rooms)]
    return sim


def clean(sim: Sim, room: str, seconds: float = 60) -> None:
    sim.step(0, vacuum=VacuumStatus.CLEANING, position=room)
    sim.step(seconds, position=room)


def job_finished(commands) -> bool:
    return any(
        isinstance(command, Emit) and command.kind == "job_finished"
        for command in commands
    )


def test_dock_trip_with_resumable_flag_and_self_resume_keeps_the_same_job():
    sim = started_job()
    job_id = sim.state.job.job_id
    clean(sim, "kitchen")
    clean(sim, "toilet")

    # 12:32:41 returning with flag resumable; 12:33:45 docked and the flag is
    # already back to none, which used to end the job 60 s later.
    sim.step(0, vacuum=VacuumStatus.RETURNING, flag="resumable", position=None)
    docked = sim.step(
        64, vacuum=VacuumStatus.DOCKED, flag=None, dock_status="emptying"
    )

    assert sim.state.job is not None
    assert sim.state.job.job_id == job_id
    assert sim.state.job.done == ["kitchen", "toilet"]
    assert sim.state.job.phase == JobPhase.RUNNING
    assert sim.state.last_result is None
    assert not job_finished(docked)
    assert clean_commands(docked) == []
    assert all(room.attempts == 0 for room in sim.state.rooms.values())

    # Still on the dock five minutes later: no resend, no finish.
    idle = sim.step(120)
    later = sim.step(180, dock_status="idle")
    assert sim.state.job.job_id == job_id
    assert sim.state.job.continuations == 0
    assert clean_commands(idle) == []
    assert clean_commands(later) == []
    assert not job_finished(idle) and not job_finished(later)
    assert sim.state.last_result is None

    # The robot resumes its own job after about five minutes and finishes.
    sim.step(0, vacuum=VacuumStatus.CLEANING, flag=None, position="study")
    assert sim.state.job.job_id == job_id
    sim.step(60, position="study")
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None, dock_status="idle")
    ended = sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.job_id == job_id
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen", "toilet", "study")
    assert sim.state.last_result.failed == ()
    assert sim.state.job is None
    assert not clean_commands(ended)
    assert all(room.attempts == 0 for room in sim.state.rooms.values())


def test_robot_that_never_resumes_gets_one_continuation_then_the_job_can_end():
    sim = started_job(("kitchen", "toilet", "study"))
    job_id = sim.state.job.job_id
    clean(sim, "kitchen")
    clean(sim, "toilet")

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None, dock_status="idle")

    # Just under ten minutes docked: nothing is sent.
    assert clean_commands(sim.step(599)) == []
    assert sim.state.job is not None

    continued = sim.step(1)
    assert clean_commands(continued) == [CleanRooms(("study",))]
    assert not job_finished(continued)
    assert sim.state.job.job_id == job_id
    assert sim.state.job.phase == JobPhase.DISPATCHED
    assert sim.state.job.dispatched == ("study",)
    assert sim.state.job.continuations == 1
    assert sim.state.job.dispatch_since == sim.now
    assert sim.state.last_result is None
    assert all(room.attempts == 0 for room in sim.state.rooms.values())

    # The resend starts the study but the robot gives up and docks again.
    sim.step(0, vacuum=VacuumStatus.CLEANING, position="study")
    sim.step(10, position="study")
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None, dock_status="idle")

    # The single continuation is spent, so the second wait ends the job.
    assert clean_commands(sim.step(599)) == []
    assert sim.state.job is not None
    ended = sim.step(1)

    assert sim.state.last_result.job_id == job_id
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen", "toilet")
    assert sim.state.last_result.failed == ("study",)
    assert sim.state.rooms["study"].attempts == 1
    assert sim.state.rooms["kitchen"].attempts == 0
    assert sim.state.rooms["toilet"].attempts == 0
    assert job_finished(ended)

    # A fresh job may start on the same tick: the robot has been docked long
    # enough for dock_dwell, and the study is still due.
    assert sim.state.job is not None
    assert sim.state.job.job_id != job_id
    assert sim.state.job.rooms == ("study",)
    assert sim.state.job.continuations == 0


def test_short_self_resume_is_not_a_continuation():
    sim = started_job(("kitchen", "toilet"))
    job_id = sim.state.job.job_id
    clean(sim, "kitchen")

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None, dock_status="emptying")
    resumed = sim.step(16, vacuum=VacuumStatus.CLEANING, position="toilet")

    assert sim.state.job.job_id == job_id
    assert sim.state.job.phase == JobPhase.RUNNING
    assert sim.state.job.continuations == 0
    assert clean_commands(resumed) == []
    assert not job_finished(resumed)

    sim.step(60, position="toilet")
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen", "toilet")


def test_dock_after_every_room_is_credited_still_ends_after_the_settle():
    sim = started_job(("kitchen", "toilet"))
    clean(sim, "kitchen")
    clean(sim, "toilet")

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    docked = sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    assert sim.state.job is not None
    assert sim.state.job.done == ["kitchen", "toilet"]
    assert sim.state.job.continuations == 0
    assert clean_commands(docked) == []

    before = sim.step(59)
    assert clean_commands(before) == []
    assert sim.state.job is not None
    sim.step(1)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen", "toilet")
    assert sim.state.last_result.failed == ()
    assert all(room.attempts == 0 for room in sim.state.rooms.values())


def test_status_says_docked_mid_clean_not_settling():
    sim = started_job(("kitchen", "toilet"))
    clean(sim, "kitchen")

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None, dock_status="emptying")

    view = sim.view()
    assert view.sentence == "Docked mid-clean (emptying); will carry on with Toilet"
    assert view.phase == "cleaning"
    assert view.remaining == ("toilet",)


def test_error_on_the_dock_wins_over_the_vacuum_gate():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(
        cfg,
        make_state(cfg, at(13, 45)),
        at(13, 45),
        vacuum=VacuumStatus.ERROR,
        vacuum_since=at(13, 38),
        error="Auto-Empty Dock dust bag full or dust duct clogged",
        dock_status="idle",
    )

    view = sim.view()
    assert view.failed_gates[:2] == ("error", "vacuum")
    assert (
        view.sentence
        == "Waiting: Robot error: Auto-Empty Dock dust bag full or dust duct clogged"
    )
