"""Tiny timeline simulator: advance a fake clock and robot state, call reconcile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from core import describe, new_state, reconcile
from core.models import (
    CleanRooms,
    Config,
    Notify,
    RoomConfig,
    Snapshot,
    State,
    VacuumStatus,
)

TZ = timezone(timedelta(hours=11))
DAY = date(2026, 11, 12)  # Thursday, the incident day.


def at(hour: int, minute: int = 0, second: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=TZ)


def make_rooms(
    room_ids: tuple[str, ...], *, blockers: bool = True
) -> tuple[RoomConfig, ...]:
    return tuple(
        RoomConfig(
            room_id=room_id,
            name=room_id.replace("_", " ").title(),
            position_values=(room_id,),
            blockers=(f"binary_sensor.{room_id}_blocked",) if blockers else (),
        )
        for room_id in room_ids
    )


def make_cfg(room_ids: tuple[str, ...] = ("kitchen", "toilet"), **overrides) -> Config:
    fields = dict(
        rooms=make_rooms(room_ids),
        global_blockers=("binary_sensor.house_quiet",),
    )
    fields.update(overrides)
    return Config(**fields)


def make_state(
    cfg: Config,
    now: datetime,
    *,
    fresh: tuple[str, ...] = (),
    smart_clean: bool = True,
) -> State:
    state = new_state(cfg)
    state.smart_clean = smart_clean
    for room in cfg.rooms:
        if room.room_id in fresh:
            state.rooms[room.room_id].last_cleaned = at(9, 30, day=now.date())
    return state


@dataclass
class Sim:
    cfg: Config
    state: State
    now: datetime
    vacuum: VacuumStatus = VacuumStatus.DOCKED
    vacuum_since: datetime | None = None
    flag: str | None = None
    error: str | None = None
    dock_status: str | None = None
    dock_ok: bool = True
    battery: int | None = 100
    position: str | None = None
    active_blockers: dict[str, str] | None = None
    fan: str | None = None
    mode: str | None = None
    commands: tuple = ()

    def __post_init__(self) -> None:
        if self.vacuum_since is None:
            self.vacuum_since = self.now - timedelta(minutes=10)
        if self.active_blockers is None:
            self.active_blockers = {}
        if self.fan is None:
            self.fan = self.cfg.clean_fan

    def snapshot(self) -> Snapshot:
        return Snapshot(
            now=self.now,
            vacuum=self.vacuum,
            vacuum_since=self.vacuum_since,
            flag=self.flag,
            error=self.error,
            dock_status=self.dock_status,
            dock_ok=self.dock_ok,
            battery=self.battery,
            position=self.position,
            active_blockers=dict(self.active_blockers),
            fan=self.fan,
            mode=self.mode,
        )

    def step(self, seconds: float = 0, **changes) -> tuple:
        self.now += timedelta(seconds=seconds)
        vacuum = changes.pop("vacuum", None)
        if vacuum is not None and vacuum != self.vacuum:
            self.vacuum = vacuum
            self.vacuum_since = self.now
        for key, value in changes.items():
            setattr(self, key, value)
        result = reconcile(self.state, self.snapshot(), self.cfg)
        self.state = result.state
        self.commands = result.commands
        return self.commands

    def view(self):
        return describe(self.state, self.snapshot(), self.cfg)


def clean_commands(commands) -> list[CleanRooms]:
    return [command for command in commands if isinstance(command, CleanRooms)]


def notifies(commands, kind=None) -> list[Notify]:
    found = [command for command in commands if isinstance(command, Notify)]
    if kind is None:
        return found
    return [command for command in found if command.kind == kind]
