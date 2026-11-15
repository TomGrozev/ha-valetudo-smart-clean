"""Regression tests for the round-2 review findings."""

from datetime import timedelta

from core import apply_intent, state_from_dict, state_to_dict
from core.models import (
    CleanNow,
    CleanRooms,
    JobEnd,
    JobPhase,
    NotifyKind,
    ReturnHome,
    VacuumStatus,
)

from .harness import Sim, at, clean_commands, make_cfg, make_state, notifies


def run_intent(sim: Sim, intent):
    result = apply_intent(sim.state, intent, sim.snapshot(), sim.cfg)
    sim.state = result.state
    sim.commands = result.commands
    return result.commands


def start_job(rooms=("kitchen", "toilet")):
    cfg = make_cfg(rooms)
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    assert any(isinstance(command, CleanRooms) for command in sim.step(0))
    sim.step(5, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)
    return sim


def test_return_home_is_retried_while_paused():
    sim = start_job()
    sim.step(5, vacuum=VacuumStatus.PAUSED)

    paused = sim.step(301)
    assert [type(command) for command in paused].count(ReturnHome) == 1
    assert sim.state.job.return_home_count == 1

    assert [
        command for command in sim.step(179) if isinstance(command, ReturnHome)
    ] == []
    assert [
        command for command in sim.step(2) if isinstance(command, ReturnHome)
    ] != []
    assert sim.state.job.return_home_count == 2


def test_return_home_gives_up_after_three_sends():
    sim = start_job()
    sim.step(5, active_blockers={"binary_sensor.house_quiet": "House quiet"})
    assert sim.state.job.return_home_count == 1

    sent = 1
    for _ in range(2):
        assert [
            command
            for command in sim.step(180)
            if isinstance(command, ReturnHome)
        ] != []
        sent += 1
    assert sent == 3
    assert sim.state.job.return_home_count == 3

    ended = sim.step(180)
    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.failed == ("kitchen", "toilet")
    assert sim.state.rooms["kitchen"].attempts == 1
    assert sim.state.rooms["toilet"].attempts == 1
    stopped = notifies(ended, NotifyKind.JOB_STOPPED)
    assert len(stopped) == 1
    assert "Willy did not go home; check it" in stopped[0].message


def test_dispatch_timeout_ages_while_paused():
    sim = start_job()
    sim.step(5, vacuum=VacuumStatus.PAUSED)
    run_intent(sim, CleanNow(("toilet",), True))
    assert sim.state.job is not None
    assert sim.state.job.phase == JobPhase.DISPATCHED

    ended = sim.step(181)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.DISPATCH_FAILED
    assert len(notifies(ended, NotifyKind.DISPATCH_FAILED)) == 1


def test_stale_resumable_flag_does_not_block_automatic_dispatch():
    cfg = make_cfg(
        ("kitchen",),
        resume_timeout=timedelta(minutes=30),
    )
    sim = Sim(
        cfg,
        make_state(cfg, at(10, 0)),
        at(10, 0),
        vacuum=VacuumStatus.DOCKED,
        vacuum_since=at(9, 50),
        flag="resumable",
    )

    assert clean_commands(sim.step(0)) == []
    assert clean_commands(sim.step(19 * 60)) == []
    assert clean_commands(sim.step(61)) == [CleanRooms(("kitchen",))]
    assert sim.state.job is not None


def test_return_home_cap_survives_restart():
    sim = start_job()
    sim.step(5, active_blockers={"binary_sensor.house_quiet": "House quiet"})
    sim.state.job.return_home_count = 2
    sim.state.job.return_home_failed = True
    sim.state.job.dispatch_since = sim.now

    restored = state_from_dict(state_to_dict(sim.state), sim.cfg)

    assert restored.job is not None
    assert restored.job.return_home_count == 2
    assert restored.job.return_home_failed is True
    assert restored.job.dispatch_since == sim.now
