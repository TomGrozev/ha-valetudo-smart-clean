"""Global and per-room blockers during a job."""

from dataclasses import replace

from core.models import (
    CleanRooms,
    JobEnd,
    JobPhase,
    NotifyKind,
    ReturnHome,
    RoomStatus,
    StopCause,
    VacuumStatus,
)

from .harness import Sim, at, clean_commands, make_cfg, make_state, notifies


def test_global_blocker_mid_job_returns_home_then_notifies_with_actions():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    blocked = sim.step(
        0, active_blockers={"binary_sensor.house_quiet": "House is quiet"}
    )
    assert any(isinstance(command, ReturnHome) for command in blocked)
    assert sim.state.job is not None
    assert sim.state.job.phase == JobPhase.RETURNING
    assert sim.state.job.stop == StopCause.BLOCKED
    assert all(room.attempts == 0 for room in sim.state.rooms.values())

    sim.step(0, vacuum=VacuumStatus.DOCKED)
    finished = sim.step(60)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.failed == ()
    assert sim.state.cooldown_until is not None
    assert sim.state.stats.stops == 1

    stopped = notifies(finished, NotifyKind.JOB_STOPPED)
    assert len(stopped) == 1
    actions = [action.action for action in stopped[0].actions]
    assert actions == [
        "VSC_CLEAN_NOW:kitchen,toilet",
        "VSC_SKIP:kitchen",
        "VSC_SKIP:toilet",
    ]
    assert stopped[0].actions[0].title == "Clean anyway"
    assert "House is quiet" in stopped[0].message


def test_room_blocker_on_non_current_room_drops_it_and_redispatches():
    cfg = make_cfg(("kitchen", "toilet", "study"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    commands = sim.step(
        0, active_blockers={"binary_sensor.toilet_blocked": "Someone is in the toilet"}
    )
    assert sim.state.job is not None
    assert sim.state.job.dropped == ["toilet"]
    assert sim.state.job.dispatched == ("kitchen", "study")
    assert sim.state.rooms["toilet"].attempts == 0
    assert clean_commands(commands) == [CleanRooms(("kitchen", "study"))]


def test_blocked_current_room_below_threshold_is_dropped():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="toilet")
    sim.step(10)

    commands = sim.step(
        0, active_blockers={"binary_sensor.toilet_blocked": "Someone is in the toilet"}
    )
    assert sim.state.job is not None
    assert sim.state.job.dropped == ["toilet"]
    assert sim.state.rooms["toilet"].last_cleaned is None
    assert clean_commands(commands) == [CleanRooms(("kitchen",))]
    assert sim.state.job.phase == JobPhase.RUNNING

    sim.step(0, vacuum=VacuumStatus.DOCKED)
    sim.step(60)
    assert sim.state.job is None
    assert sim.state.last_result.failed == ("kitchen",)
    assert sim.state.rooms["toilet"].attempts == 0


def test_blocked_room_over_threshold_when_robot_left_counts_done():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="toilet")
    sim.step(40)

    sim.step(
        0,
        position="kitchen",
        active_blockers={"binary_sensor.toilet_blocked": "Someone is in the toilet"},
    )
    job = sim.state.job
    assert job is not None
    assert job.done == ["toilet"]
    assert job.dropped == []
    assert sim.state.rooms["toilet"].last_cleaned == sim.now
    assert sim.state.rooms["toilet"].attempts == 0


def _cfg_with_hard_toilet():
    cfg = make_cfg(("kitchen", "toilet"))
    rooms = tuple(
        replace(room, hard_blockers=("binary_sensor.child_sleeping",))
        if room.room_id == "toilet"
        else room
        for room in cfg.rooms
    )
    return replace(cfg, rooms=rooms)


def test_clean_when_occupied_does_not_bypass_hard_room_blocker():
    cfg = _cfg_with_hard_toilet()
    state = make_state(cfg, at(10, 0))
    state.clean_when_occupied = True
    sim = Sim(cfg, state, at(10, 0))
    sim.active_blockers = {"binary_sensor.child_sleeping": "Child sleeping"}

    assert clean_commands(sim.step(0)) == [CleanRooms(("kitchen",))]
    toilet = next(room for room in sim.view().rooms if room.room_id == "toilet")
    assert toilet.status == RoomStatus.WAITING
    assert toilet.reason == "Child sleeping"


def test_clean_when_occupied_does_not_bypass_global_blocker():
    cfg = make_cfg(("kitchen", "toilet"))
    state = make_state(cfg, at(10, 0))
    state.clean_when_occupied = True
    sim = Sim(cfg, state, at(10, 0))
    sim.active_blockers = {"binary_sensor.house_quiet": "House is quiet"}

    assert clean_commands(sim.step(0)) == []
    assert "blocked" in sim.view().failed_gates


def test_hard_room_blocker_mid_job_applies_with_clean_when_occupied():
    cfg = _cfg_with_hard_toilet()
    state = make_state(cfg, at(10, 0))
    state.clean_when_occupied = True
    sim = Sim(cfg, state, at(10, 0))
    assert clean_commands(sim.step(0)) == [CleanRooms(("kitchen", "toilet"))]
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    commands = sim.step(
        0, active_blockers={"binary_sensor.child_sleeping": "Child sleeping"}
    )
    assert sim.state.job is not None
    assert sim.state.job.dropped == ["toilet"]
    assert clean_commands(commands) == [CleanRooms(("kitchen",))]


def test_global_blocker_mid_job_applies_with_clean_when_occupied():
    cfg = make_cfg(("kitchen", "toilet"))
    state = make_state(cfg, at(10, 0))
    state.clean_when_occupied = True
    sim = Sim(cfg, state, at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")

    commands = sim.step(
        0, active_blockers={"binary_sensor.house_quiet": "House is quiet"}
    )
    assert any(isinstance(command, ReturnHome) for command in commands)
    assert sim.state.job.stop == StopCause.BLOCKED
