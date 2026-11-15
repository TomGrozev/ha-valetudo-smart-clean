"""Replay of 2026-11-12: the toilet must be dispatched once, not twenty times."""

from datetime import timedelta

from core.models import CleanRooms, VacuumStatus

from .harness import Sim, at, clean_commands, make_cfg, make_state


def run_afternoon(min_presence: timedelta):
    cfg = make_cfg(("kitchen", "toilet"), min_presence=min_presence)
    state = make_state(cfg, at(13, 22), fresh=("kitchen",))
    sim = Sim(
        cfg,
        state,
        at(13, 22),
        vacuum=VacuumStatus.DOCKED,
        position=None,
        vacuum_since=at(13, 20),
    )
    dispatches: list[CleanRooms] = []

    # Start at 13:22 like the incident; the first dispatch is the only toilet job.
    dispatches.extend(clean_commands(sim.step(30)))
    assert sim.state.job is not None
    assert dispatches == [CleanRooms(("toilet",))]

    # The robot cleans the toilet while the position source flickers to the
    # hallway every 15-48 s, exactly the pattern that defeated the old 120 s
    # continuous-presence rule.
    sim.step(0, vacuum=VacuumStatus.CLEANING, position="toilet")
    sim.step(30)
    sim.step(0, position="hallway")
    sim.step(15)
    sim.step(0, position="toilet")
    sim.step(48)
    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
    sim.step(60)

    assert sim.state.job is None

    # Keep ticking every 15-60 s until the window closes; nothing may re-dispatch.
    tick = 0
    while sim.now < at(17, 0):
        left = (at(17, 0) - sim.now).total_seconds()
        seconds = min(15 + (tick * 17) % 46, max(left, 0))
        tick += 1
        dispatches.extend(clean_commands(sim.step(seconds)))
        if seconds == 0:
            break

    return dispatches


def test_replay_2026_11_12_toilet_is_dispatched_once_all_afternoon():
    dispatches = run_afternoon(timedelta(seconds=30))

    assert dispatches == [CleanRooms(("toilet",))]


def test_replay_fails_under_a_120_second_continuous_rule():
    # 120 s of cumulative presence is still too much for the flickering 30 s and
    # 48 s stints, so the toilet is retried and the old rule cannot pass.
    dispatches = run_afternoon(timedelta(seconds=120))

    assert len(dispatches) > 1
