"""Persistence: a restart mid-job keeps the job and caps the presence gap."""

import json

from core import (
    new_state,
    state_from_dict,
    state_to_dict,
)
from core.models import (
    JobEnd,
    VacuumStatus,
)

from .harness import Sim, at, make_cfg, make_state


def test_restart_mid_job_continues_and_caps_presence_gap():
    cfg = make_cfg(("kitchen", "toilet"))
    sim = Sim(cfg, make_state(cfg, at(10, 0)), at(10, 0))
    sim.step(0)
    sim.step(3, vacuum=VacuumStatus.CLEANING, position="kitchen")
    sim.step(10)
    assert sim.state.job is not None
    assert sim.state.job.presence == {"kitchen": 10.0}

    saved = state_to_dict(sim.state)
    assert json.loads(json.dumps(saved)) == saved
    restored = state_from_dict(saved, cfg)
    assert restored.job is not None
    assert restored.job.presence == {"kitchen": 10.0}

    restarted = Sim(cfg, restored, sim.now, vacuum=VacuumStatus.CLEANING, position="kitchen")
    # HA was down for 1000 s; at most max_presence_gap (90 s) is credited.
    restarted.step(1000)
    assert restarted.state.job is not None
    assert restarted.state.job.presence["kitchen"] == 100.0

    restarted.step(0, vacuum=VacuumStatus.RETURNING, position=None)
    restarted.step(60, vacuum=VacuumStatus.DOCKED)
    restarted.step(60)
    assert restarted.state.job is None
    assert restarted.state.last_result.end == JobEnd.COMPLETED
    assert restarted.state.last_result.done == ("kitchen",)


def test_state_from_dict_adds_missing_and_drops_unknown_rooms():
    cfg = make_cfg(("kitchen", "toilet"))
    state = new_state(cfg)
    data = state_to_dict(state)
    data["rooms"].pop("toilet")
    data["rooms"]["garage"] = {"attempts": 5}
    data["room_enabled"].pop("toilet")
    data["room_enabled"]["garage"] = True

    restored = state_from_dict(data, cfg)
    assert set(restored.rooms) == {"kitchen", "toilet"}
    assert restored.rooms["toilet"].attempts == 0
    assert set(restored.room_enabled) == {"kitchen", "toilet"}
    assert restored.room_enabled["toilet"] is True
