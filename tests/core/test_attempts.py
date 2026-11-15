"""Failed attempts: two tries per room per day, then skipped until tomorrow."""

from datetime import timedelta

from core.models import (
    CleanRooms,
    JobEnd,
    NotifyKind,
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
    first_end = sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    sim.step(60)
    assert clean_commands(first_end) == []
    assert sim.state.last_result is not None
    assert sim.state.last_result.end == JobEnd.COMPLETED
    assert sim.state.last_result.failed == ("toilet",)
    assert sim.state.rooms["toilet"].attempts == 1
    assert sim.state.cooldown_until is None

    # Dock dwell passes; only the toilet is still due.
    assert clean_commands(sim.step(120)) == [CleanRooms(("toilet",))]
    sim.step(3, vacuum=VacuumStatus.CLEANING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    second_end = sim.step(60)

    assert sim.state.job is None
    assert sim.state.last_result.end == JobEnd.COMPLETED
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
