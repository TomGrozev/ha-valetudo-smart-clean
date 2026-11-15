"""Termination invariant: random day, no room can be dispatched forever."""

from __future__ import annotations

import math
import random
from datetime import timedelta

from core.models import (
    Emit,
    JobOrigin,
    JobPhase,
    VacuumStatus,
)

from .harness import Sim, at, clean_commands, make_cfg, make_state

ROOMS = ("kitchen", "toilet", "study", "bedroom")


def _run_day(seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    cfg = make_cfg(
        ROOMS,
        retry_delay=timedelta(minutes=60),
        max_attempts=2,
    )
    sim = Sim(cfg, make_state(cfg, at(9, 0)), at(9, 0))
    counts = {room: 0 for room in ROOMS}

    job_id: str | None = None
    planned: list[str] = []
    planned_ticks = 0
    ticks_left = 0
    pausing = 0

    while sim.now < at(17, 0):
        job = sim.state.job
        if job is None:
            commands = sim.step(60, vacuum=VacuumStatus.DOCKED, position=None)
        elif job.phase == JobPhase.DISPATCHED:
            rooms = job.dispatched or job.rooms
            if rooms and rng.random() < 0.8:
                commands = sim.step(
                    60,
                    vacuum=VacuumStatus.CLEANING,
                    position=rng.choice(rooms),
                )
            else:
                commands = sim.step(60, vacuum=VacuumStatus.DOCKED)
        elif job.phase == JobPhase.RETURNING:
            commands = sim.step(
                60, vacuum=VacuumStatus.DOCKED, position=None, flag=None
            )
        elif pausing:
            pausing += 60
            if pausing >= 420:
                pausing = 0
                commands = sim.step(60, vacuum=VacuumStatus.CLEANING)
            else:
                commands = sim.step(60, vacuum=VacuumStatus.PAUSED)
        else:
            if job.job_id != job_id:
                job_id = job.job_id
                left = [room for room in job.rooms if room not in job.done]
                planned = rng.sample(left, rng.randint(0, len(left)))
                planned_ticks = rng.randint(2, 7)
                ticks_left = 0
            roll = rng.random()
            if planned and ticks_left >= planned_ticks:
                commands = sim.step(
                    60, vacuum=VacuumStatus.DOCKED, position=None
                )
            elif roll < 0.015:
                commands = sim.step(
                    60,
                    vacuum=VacuumStatus.ERROR,
                    error="Random failure",
                    position=job.last_position,
                )
            elif roll < 0.035:
                pausing = 1
                commands = sim.step(
                    60, vacuum=VacuumStatus.PAUSED, position=job.last_position
                )
            elif roll < 0.06:
                commands = sim.step(
                    60,
                    position=job.last_position,
                    active_blockers={"binary_sensor.house_quiet": "House is quiet"},
                )
            else:
                if roll < 0.10:
                    left = [
                        room
                        for room in job.rooms
                        if room not in job.done and room not in job.dropped
                    ]
                    if left:
                        blocked = rng.choice(left)
                        commands = sim.step(
                            60,
                            position=job.last_position,
                            active_blockers={
                                f"binary_sensor.{blocked}_blocked": "Occupied"
                            },
                        )
                        ticks_left += 1
                        continue
                ticks_left += 1
                commands = sim.step(
                    60,
                    vacuum=VacuumStatus.CLEANING,
                    position=rng.choice(planned) if planned else None,
                    active_blockers={},
                    flag=None,
                )

        assert len(clean_commands(commands)) <= 1
        if any(isinstance(c, Emit) and c.kind == "job_started" for c in commands):
            if sim.state.job is not None and sim.state.job.origin == JobOrigin.AUTO:
                for room in sim.state.job.rooms:
                    counts[room] += 1
    return counts


def test_termination_invariant_over_many_random_days():
    cfg = make_cfg(ROOMS, retry_delay=timedelta(minutes=60), max_attempts=2)
    window = cfg.window_end.hour * 3600 + cfg.window_end.minute * 60
    window -= cfg.window_start.hour * 3600 + cfg.window_start.minute * 60
    cap = cfg.max_attempts + math.ceil(window / cfg.retry_delay.total_seconds())

    for seed in range(40):
        counts = _run_day(seed)
        for room, count in counts.items():
            assert count <= cap, f"seed {seed}, {room} dispatched {count} > {cap}"
