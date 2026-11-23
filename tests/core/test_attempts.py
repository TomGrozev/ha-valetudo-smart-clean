"""Failed attempts: two tries per room per day, then skipped until tomorrow."""

from datetime import timedelta

from core.models import (
    CleanRooms,
    JobEnd,
    NotifyKind,
    ReturnHome,
    VacuumStatus,
)

from .harness import Sim, at, clean_commands, make_cfg, make_state, notifies


def test_room_never_visited_is_retried_then_skipped():
    cfg = make_cfg(
        ("kitchen", "toilet"),
        max_attempts=2,
        retry_delay=timedelta(minutes=60),
    )
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))

    assert clean_commands(sim.step(0)) == [CleanRooms(("kitchen", "toilet"))]
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(60)
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)

    # The robot went home with the toilet still to do. That is a dock trip, not
    # a finish: the job stays open and the toilet is sent again once.
    assert sim.state.job is not None
    assert sim.state.last_result is None
    assert sim.state.rooms["toilet"].attempts == 0
    assert clean_commands(sim.step(600)) == [CleanRooms(("toilet",))]
    assert sim.state.job.continuations == 1

    # The robot starts the toilet but not for long enough, then docks again.
    sim.step(0, vacuum=VacuumStatus.CLEANING, position="toilet")
    sim.step(10, position="toilet")
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)

    # The one continuation is spent, so the second wait ends the job and the
    # toilet uses its first attempt.
    sim.step(599)
    sim.step(1)
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.failed == ("toilet",)
    assert sim.state.rooms["toilet"].attempts == 1
    assert sim.state.rooms["kitchen"].attempts == 0

    # The robot has been docked long enough that the retry starts at once; the
    # second attempt runs out when the robot pauses in the toilet.
    assert clean_commands(sim.commands) == [CleanRooms(("toilet",))]
    sim.step(0, vacuum=VacuumStatus.CLEANING, position="toilet")
    sim.step(10, position="toilet")
    sim.step(0, vacuum=VacuumStatus.PAUSED, position="toilet")
    paused = sim.step(301)
    assert any(isinstance(command, ReturnHome) for command in paused)
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    second_end = sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.INTERRUPTED
    assert sim.state.last_result.failed == ("toilet",)
    toilet = sim.state.rooms["toilet"]
    assert toilet.attempts == 2
    assert toilet.skipped_on == sim.now.date()
    assert sim.state.cooldown_until is not None
    assert sim.state.cooldown_until > sim.now

    skipped = notifies(second_end, NotifyKind.ROOM_SKIPPED)
    assert len(skipped) == 1
    assert [action.action for action in skipped[0].actions] == ["VSC_RETRY:toilet"]
    assert skipped[0].actions[0].title == "Try again"

    # No third dispatch anywhere later that day.
    assert clean_commands(sim.step(7200)) == []
