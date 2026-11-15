"""Pure core for the valetudo_smart_clean integration.

Nothing here imports homeassistant. The Home Assistant layer builds a Snapshot,
calls reconcile/apply_intent, persists the returned State and runs the Commands.
"""

from __future__ import annotations

from .describe import describe
from .intents import apply_intent, parse_action
from .models import Config, RoomState, State
from .reconcile import reconcile
from .serialize import state_from_dict, state_to_dict

__all__ = [
    "new_state",
    "reconcile",
    "apply_intent",
    "describe",
    "parse_action",
    "state_to_dict",
    "state_from_dict",
]


def new_state(cfg: Config) -> State:
    state = State()
    for room in cfg.rooms:
        state.room_enabled[room.room_id] = True
        state.rooms[room.room_id] = RoomState()
    return state
