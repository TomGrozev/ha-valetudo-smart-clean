"""Quiet-travel fan, daily report, and describe() output."""

from datetime import timedelta

from core.models import (
    JobPhase,
    NotifyKind,
    SetFan,
    VacuumStatus,
)

from .harness import Sim, at, make_cfg, make_state, notifies


def test_quiet_travel_fan_only_changes():
    cfg = make_cfg(
        ("kitchen", "toilet"),
        quiet_travel=True,
        travel_fan="low",
        clean_fan="medium",
    )
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0), fan="medium")
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    assert not any(isinstance(command, SetFan) for command in sim.commands)
    sim.step(40)
    assert not any(isinstance(command, SetFan) for command in sim.commands)

    travel = sim.step(0, position="hallway", fan="medium")
    assert [command.speed for command in travel if isinstance(command, SetFan)] == ["low"]
    sim.step(10, fan="low")
    assert not any(isinstance(command, SetFan) for command in sim.commands)

    back = sim.step(0, position="toilet", fan="low")
    assert [command.speed for command in back if isinstance(command, SetFan)] == ["medium"]
    sim.step(40, fan="medium")
    assert not any(isinstance(command, SetFan) for command in sim.commands)

    sim.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    sim.step(60, vacuum=VacuumStatus.DOCKED, fan="low")
    finished = sim.step(60)
    assert [command.speed for command in finished if isinstance(command, SetFan)] == [
        "medium"
    ]
    assert sim.state.job is None
    assert sim.state.last_result.done == ("kitchen", "toilet")


def test_daily_report_sent_once_after_window_end():
    cfg = make_cfg(("kitchen", "toilet"), daily_report=True)
    sim = Sim(
        cfg,
        make_state(cfg, at(17, 5), fresh=("kitchen", "toilet")),
        at(17, 5),
    )
    first = sim.step(0)
    reports = notifies(first, NotifyKind.DAILY_REPORT)
    assert len(reports) == 1
    assert reports[0].message.startswith("2 of 2 rooms cleaned today")
    assert sim.state.report_sent_on == sim.now.date()

    second = sim.step(60)
    assert notifies(second, NotifyKind.DAILY_REPORT) == []


def test_describe_lists_every_failed_gate_in_order():
    cfg = make_cfg(("kitchen", "toilet"))
    state = make_state(cfg, at(8, 0), fresh=("kitchen", "toilet"), smart_clean=False)
    state.cooldown_until = at(9, 10)
    sim = Sim(
        cfg,
        state,
        at(8, 0),
        vacuum=VacuumStatus.PAUSED,
        vacuum_since=at(8, 0),
        battery=10,
        error="Dust bin missing",
        dock_ok=False,
        flag="emptying",
        active_blockers={"binary_sensor.house_quiet": "House is quiet"},
    )
    view = sim.view()
    assert view.failed_gates == (
        "smart_clean",
        "cooldown",
        "window",
        "vacuum",
        "dock_dwell",
        "battery",
        "error",
        "dock",
        "flag",
        "blocked",
        "nothing_due",
    )
    assert view.sentence == "Smart clean is off"
    assert view.phase == "off"


def test_describe_sentences():
    cfg = make_cfg(("kitchen", "toilet"))
    now = at(10, 0)

    due = Sim(cfg, make_state(cfg, now), now, vacuum=VacuumStatus.IDLE)
    assert due.view().sentence == "Waiting: Robot is not docked"

    off = Sim(cfg, make_state(cfg, now, smart_clean=False), now)
    assert off.view().sentence == "Smart clean is off"
    assert off.view().phase == "off"

    quiet = Sim(cfg, make_state(cfg, now), now)
    quiet.active_blockers = {"binary_sensor.house_quiet": "House is quiet"}
    assert quiet.view().sentence == "Waiting: House is quiet"
    assert quiet.view().phase == "waiting"

    cooling = Sim(cfg, make_state(cfg, now), now)
    cooling.state.cooldown_until = at(14, 5)
    assert cooling.view().sentence == "Retrying at 14:05"
    assert cooling.view().phase == "cooldown"

    fresh = Sim(cfg, make_state(cfg, now, fresh=("kitchen", "toilet")), now)
    assert fresh.view().sentence == "All rooms are clean for today"
    assert fresh.view().phase == "idle"

    later_state = make_state(cfg, at(18, 0), fresh=("kitchen",))
    later = Sim(cfg, later_state, at(18, 0))
    assert later.view().sentence == "Next: Toilet at 09:00 tomorrow"
    assert later.view().next_attempt_at == at(9, 0, day=at(18, 0).date() + timedelta(days=1))

    job_sim = Sim(cfg, make_state(cfg, now), now)
    job_sim.step(0)
    job_sim.step(1, vacuum=VacuumStatus.CLEANING)
    assert job_sim.state.job.phase == JobPhase.RUNNING
    assert job_sim.view().sentence == "Cleaning Kitchen, then Toilet"
    assert job_sim.view().job_rooms == ("kitchen", "toilet")
    assert job_sim.view().remaining == ("kitchen", "toilet")
