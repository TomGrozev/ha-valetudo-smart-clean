# Valetudo Smart Clean

A Home Assistant integration that cleans the rooms you care about, one job at a
time, and knows the difference between "the robot docked" and "the room is
actually clean".

It is built for a Valetudo robot (the MQTT integration) that exposes a vacuum
entity, a status flag sensor, an error sensor, a dock status sensor, dock
consumable sensors, fan and mode selects, and a map camera whose
`vacuum_position.in_room` attribute names the room the robot is in.

The integration replaces a pile of UI automations with one persisted planner.
It survives restarts, missing events and dropped service calls because every
decision is recomputed from the current entity states on every change and every
15 seconds.

## What it does

- Works out which rooms are due today, in your chosen order.
- Waits for the robot to be docked, charged, idle, error-free, with a healthy
  dock, inside the cleaning window, and not blocked by occupancy sensors.
- Sends the whole due list with `vacuum.clean_area` and remembers what it sent.
- Follows the robot room by room, credits the time it actually spends cleaning,
  and marks a room done only when it has had enough presence.
- Retries a room that did not get clean, up to twice a day, then skips it until
  tomorrow and asks you on your phone whether to try again.
- Stops and sends the robot home when a blocker turns on, when it pauses too
  long, or when it reports an error.
- Optionally drops the fan while crossing rooms that are not part of the job.

## How it decides a room is clean

A room is done when two things are true:

1. The robot has spent enough time **cleaning inside that room**. Time is
   credited from consecutive position observations while the robot is
   `cleaning`, capped at 90 seconds per gap so an HA restart cannot invent
   presence. Short flickers between the toilet and the hallway add up instead of
   resetting the clock.
2. The robot is **not in that room at the moment of judging**. At the end of a
   job the robot is docked, so no room is "current".

"Enough time" is the larger of 30 seconds and 40% of the typical time that room
needs, learned from its last five completed cleans. A small room like the toilet
therefore needs roughly half a minute of real cleaning, not two continuous
minutes.

This is why the integration exists: Valetudo has no job identity and no "job
finished" event, and a failed segment clean ends `docked` exactly like a
successful one. Counting presence is the only evidence available without
diffing the map.

## The two-attempt rule

Each room gets **two automatic attempts per day**. An attempt is used when a
job ends with the room not clean: it completed without enough presence, the
robot errored in it, or it was paused too long. Blocked-away rooms are dropped
without using an attempt.

When a room reaches two attempts it is skipped until tomorrow, and you get a
notification with **Try again**. Tapping it clears the room's skip and attempts
and re-plans immediately. Rooms that ended up blocked or were stopped by hand
are not counted; you get a notification with **Clean anyway** and **Skip room**
buttons instead.

Every retry is separated by a cooldown (one hour by default), so the robot
cannot loop on one room. The next automatic start is also gated on the robot
sitting docked for a while, which keeps a reboot or a manual run from
triggering an immediate job.

## Entities

One device per robot, named `<vacuum name> Smart Clean`.

| Entity | What it shows |
| --- | --- |
| `switch.<...>_smart_clean` | Master switch. Off does not stop a running job. |
| `switch.<...>_clean_when_occupied` | Clean even while a blocker is on. |
| `switch.<room>` | Include or exclude a room from automatic cleaning. |
| `sensor.<...>_status` | One sentence: what is happening and why. Attributes: `phase`, `failed_gates`, `job_rooms`, `remaining`, `next_attempt_at`. |
| `sensor.<room>` | `cleaning`, `ready`, `waiting`, `fresh`, `skipped` or `excluded`. Attributes: `reason`, `last_cleaned`, `estimate_s`, `attempts_today`. |
| `sensor.<...>_last_job` | How the last job ended, with `done`, `failed`, `dropped` and `reason`. |

## Services

All services accept an optional `config_entry_id` when more than one robot is
configured.

| Service | Fields | Effect |
| --- | --- | --- |
| `valetudo_smart_clean.clean_rooms` | `rooms` (areas, in order), `ignore_blockers` (default true) | Clean those rooms now, ignoring the window, switches, skips and attempts. |
| `valetudo_smart_clean.skip_today` | `room` | Skip a room until tomorrow. |
| `valetudo_smart_clean.retry_today` | `room` (optional) | Clear today's skip and attempts for one room, or all rooms. |
| `valetudo_smart_clean.mark_cleaned` | `room`, `when` (optional) | Record a room as cleaned without running the robot. |
| `valetudo_smart_clean.stop` | `reason` (optional) | Stop the job and send the robot home. |

## Events

The integration fires `valetudo_smart_clean` on the event bus for anything you
may want to automate around. Every event has `type` and `entry_id`.

| `type` | When |
| --- | --- |
| `job_started` | A job was dispatched. |
| `room_done` | A room reached its presence threshold. |
| `room_skipped` | A room used its last attempt and is skipped until tomorrow. |
| `job_finished` | A job ended, with the end state and the rooms done and failed. |
| `command_failed` | A service call to the robot failed. It is not retried. |

Notifications are sent through the script you configure. The script is called
with `title`, `message`, `tag`, `level` (`active`, `time-sensitive` or
`passive`) and `actions` (a list of `{action, title}`). The action ids are
handled by the integration, so your phone buttons work as long as the companion
app fires `mobile_app_notification_action`.

## Installation

### HACS / Marketplace

1. Open **HACS → Integrations** (in Home Assistant 2026.11 this is the
   **Marketplace**) and add this repository as a **custom repository** with
   category **Integration**.
2. Install **Valetudo Smart Clean**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Valetudo Smart Clean**.

### Manual

Copy `custom_components/valetudo_smart_clean/` into your Home Assistant
`config/custom_components/` directory, restart, and add the integration.

## Configuration

### 1. The robot

Pick the Valetudo vacuum entity and the script that sends your phone
notifications. The script receives the fields listed under [Events](#events).

### 2. The entities

The next screen is pre-filled from the vacuum's device: status flag, error, dock
status, battery, dock consumables, fan and mode selects, and the map camera.
Adjust anything the discovery guessed wrong. Leave a sensor empty if you do not
have it; the related gate is then skipped.

The position source defaults to `camera.<robot>` attribute
`vacuum_position.in_room`, which Valetudo fills with the segment name the robot
is in.

### 3. The rooms

Pick Home Assistant areas, in the order the robot should clean them. Each area's
position values are pre-filled by matching the area name against the map
segments sensor, and can be edited per room in the options.

**Important:** this integration calls `vacuum.clean_area` with area ids. In Home
Assistant 2026.11 that service maps areas to Valetudo segments through the
vacuum entity's own options, so open the vacuum entity's settings once and map
each area to its segment ids. Without that mapping Home Assistant has nothing
to send to the robot.

### 4. Options

**Configure** opens a menu:

- **Schedule**: cleaning window, weekdays, mop weekdays, and the mode to use on
  each kind of day.
- **Behaviour**: battery floor, attempts per day, dock dwell, timeouts, retry
  delay, presence threshold and fraction, longest credited gap, quiet travel and
  fan speeds, daily report.
- **Rooms**: per-room position values and blockers.
- **Global blockers**: any of these being `on` pauses automatic cleaning.

Only `on` counts as a blocker. `home`, `open`, `playing` and other states are
ignored.

## Requirements

- Home Assistant 2026.11 or newer.
- A Valetudo robot integrated over MQTT, with the vacuum entity and the
  auxiliary sensors listed above. A map camera exposing
  `vacuum_position.in_room` is needed for presence tracking.
- Mobile notifications are optional but recommended; without a notify script the
  buttons never reach you and the planner simply keeps its own state.

## License

MIT. See [LICENSE](LICENSE).
