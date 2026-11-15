"""Regression tests for the round-1 review findings in the pure core."""

from datetime import timedelta

from core import apply_intent, state_from_dict, state_to_dict
from core.models import (
    CleanNow,
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


def run_intent(sim: Sim, intent):
    result = apply_intent(sim.state, intent, sim.snapshot(), sim.cfg)
    sim.state = result.state
    sim.commands = result.commands
    return result.commands


def test_docked_position_in_room_does_not_block_that_room_at_job_end():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(40)

    # A dock inside the kitchen reports the kitchen as the raw position.
    sim.step(0, vacuum=VacuumStatus.DOCKED, position="kitchen")
    assert sim.state.job is not None
    sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.done == ("kitchen",)


def test_paused_position_keeps_room_current_so_it_is_not_judged_done():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(40)

    sim.step(0, vacuum=VacuumStatus.PAUSED, position="kitchen")
    assert sim.state.job is not None
    # Over the presence threshold, but the robot is still sitting in the room.
    assert sim.state.rooms["kitchen"].last_cleaned is None
    sim.step(299)
    assert sim.state.rooms["kitchen"].last_cleaned is None

    paused = sim.step(2)
    assert any(isinstance(command, ReturnHome) for command in paused)
    assert sim.state.job.phase == JobPhase.RETURNING


def test_docked_job_survives_flag_resumable_arriving_after_dock():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(31)

    sim.step(0, vacuum=VacuumStatus.DOCKED, flag=None)
    assert sim.state.job is not None
    # The flag can lag the vacuum state by a second; that is a recharge, not a finish.
    sim.step(1, flag="resumable")
    assert sim.state.job is not None
    assert sim.state.job.phase == JobPhase.RUNNING
    sim.step(30, flag="resumable")
    assert sim.state.job is not None

    sim.step(0, flag=None)
    sim.step(60)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED


def test_resume_timeout_ends_interrupted_and_charges_unfinished_dispatched_rooms():
    cfg = make_cfg(("kitchen", "toilet"), resume_timeout=timedelta(minutes=30))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(60)

    sim.step(0, vacuum=VacuumStatus.DOCKED, flag="resumable")
    assert sim.state.job is not None
    ended = sim.step(1801, flag="resumable")

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.done == ("kitchen",)
    assert sim.state.last_result.failed == ("toilet",)
    assert sim.state.rooms["toilet"].attempts == 1
    assert sim.state.rooms["kitchen"].attempts == 0
    stopped = notifies(ended, NotifyKind.JOB_STOPPED)
    assert len(stopped) == 1
    assert "did not resume after recharging" in stopped[0].message


def test_returning_ends_on_settled_dock_even_with_resumable_flag():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    blocked = sim.step(
        0, active_blockers={"binary_sensor.house_quiet": "House is quiet"}
    )
    assert any(isinstance(command, ReturnHome) for command in blocked)
    assert sim.state.job.phase == JobPhase.RETURNING

    sim.step(0, vacuum=VacuumStatus.RETURNING, flag="resumable")
    sim.step(60, vacuum=VacuumStatus.DOCKED, flag="resumable")
    sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED


def test_clean_now_replaces_a_running_job_in_any_available_state():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(0)
    old_job_id = sim.state.job.job_id

    commands = run_intent(sim, CleanNow(("toilet",)))

    assert clean_commands(commands) == [CleanRooms(("toilet",))]
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.reason == "Replaced by a manual clean"
    assert sim.state.job is not None
    assert sim.state.job.job_id != old_job_id
    assert sim.state.job.origin == JobOrigin.MANUAL
    assert sim.state.job.phase == JobPhase.DISPATCHED
    assert sim.state.cooldown_until is None
    assert all(room.attempts == 0 for room in sim.state.rooms.values())
    assert not notifies(commands, NotifyKind.JOB_STOPPED)


def test_clean_now_is_refused_only_when_vacuum_is_error_or_unavailable():
    cfg = make_cfg(("kitchen", "toilet"))
    for status in (VacuumStatus.ERROR, VacuumStatus.UNAVAILABLE):
        sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
        sim.step(0)
        sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
        sim.step(0)
        # The intent is delivered before the next reconcile, as HA does.
        sim.vacuum = status
        job_id = sim.state.job.job_id

        assert not run_intent(sim, CleanNow(("toilet",)))
        assert sim.state.job is not None
        assert sim.state.job.job_id == job_id
        assert sim.state.job.phase == JobPhase.RUNNING


def test_dispatch_timeout_counts_only_observed_docked_or_idle_time():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    # A reconnect gap must not age the dispatch timeout.
    sim.step(180, vacuum=VacuumStatus.UNAVAILABLE)
    assert sim.state.job is not None

    sim.step(0, vacuum=VacuumStatus.DOCKED)
    sim.step(179)
    assert sim.state.job is not None
    sim.step(1)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.DISPATCH_FAILED


def test_return_home_is_retried_at_most_once_per_dispatch_timeout():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    commands = sim.step(
        0, active_blockers={"binary_sensor.house_quiet": "House is quiet"}
    )
    assert [type(command) for command in commands].count(ReturnHome) == 1

    # The robot ignores ReturnHome and keeps cleaning; retry after the timeout.
    assert [
        command for command in sim.step(179) if isinstance(command, ReturnHome)
    ] == []
    assert [
        command for command in sim.step(2) if isinstance(command, ReturnHome)
    ] != []
    assert [
        command for command in sim.step(179) if isinstance(command, ReturnHome)
    ] == []
    assert [
        command for command in sim.step(2) if isinstance(command, ReturnHome)
    ] != []


def test_blocked_current_room_dropped_mid_clean_is_never_judged_done():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(35)

    sim.step(
        0,
        active_blockers={
            "binary_sensor.kitchen_blocked": "Someone is in the kitchen"
        },
    )
    assert sim.state.job.dropped == ["kitchen"]
    assert sim.state.job.phase == JobPhase.RETURNING

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    sim.step(60)

    assert sim.state.job is None
    assert sim.state.rooms["kitchen"].last_cleaned is None
    assert sim.state.rooms["kitchen"].attempts == 0
    assert sim.state.last_result.done == ()
    assert sim.state.last_result.dropped == ("kitchen",)


def test_return_home_timestamp_and_resume_expiry_survive_restart():
    cfg = make_cfg(("kitchen",))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)
    sim.state.job.return_home_sent_at = sim.now
    sim.state.job.resume_expired = True

    restored = state_from_dict(state_to_dict(sim.state), cfg)

    assert restored.job is not None
    assert restored.job.return_home_sent_at == sim.now
    assert restored.job.resume_expired is True


def test_job_stopped_actions_include_dropped_rooms():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)

    sim.step(
        0, active_blockers={"binary_sensor.toilet_blocked": "Someone is in the toilet"}
    )
    assert sim.state.job.dropped == ["toilet"]

    sim.step(
        0,
        active_blockers={
            "binary_sensor.toilet_blocked": "Someone is in the toilet",
            "binary_sensor.house_quiet": "House is quiet",
        },
    )
    sim.step(0, vacuum=VacuumStatus.RETURNING)
    sim.step(60, vacuum=VacuumStatus.DOCKED)
    ended = sim.step(60)

    stopped = notifies(ended, NotifyKind.JOB_STOPPED)
    assert len(stopped) == 1
    actions = {action.action for action in stopped[0].actions}
    assert actions == {
        "VSC_CLEAN_NOW:kitchen,toilet",
        "VSC_SKIP:kitchen",
        "VSC_SKIP:toilet",
    }
    assert "Still to clean" in stopped[0].message
