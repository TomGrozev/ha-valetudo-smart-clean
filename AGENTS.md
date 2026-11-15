# AGENTS.md

Guidance for AI agents working in this repository.

## Layout

```
custom_components/valetudo_smart_clean/
  core/            pure Python planner and state machine (no homeassistant import)
  __init__.py      HA shell: snapshot building, Store, runner, services, listeners
  config_flow.py   config and options flows
  switch.py        smart clean, clean-when-occupied, per-room auto switches
  sensor.py        status, per-room status, last job
  const.py         keys, defaults, service names
tests/core/        pure planner tests (run without Home Assistant)
tests/ha/          Home Assistant integration tests
```

## The core / HA split

- `core/` never imports `homeassistant`. It takes a `Snapshot` and a `Config`,
  returns a `State` plus `Command`s, and is tested as plain Python.
- The HA layer only observes entities, calls the core, persists the returned
  state, and runs commands. It must not contain cleaning decisions.
- `core/models.py` is the contract between the two halves. Changing it needs
  agreement from the other side; do not add fields to dodge an HA-layer problem.

## Running tests

```
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python pytest-homeassistant==2026.11.1
.venv/bin/python -m pytest tests -q
```

`pytest-homeassistant` (not `pytest-homeassistant-custom-component`, which is
broken for current HA) supplies the `hass` fixture and pins Home Assistant.

## Conventions

- The planner is level-triggered: every tick recomputes from current state. Do
  not add "remember we already did this" flags outside the persisted state.
- New decisions go in `core/`, with a test; the HA layer gets wiring only.
