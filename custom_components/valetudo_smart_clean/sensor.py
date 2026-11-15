"""Sensors for Valetudo Smart Clean."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartCleanConfigEntry, SmartCleanRuntime, device_info
from .core.models import RoomView, StatusView

PARALLEL_UPDATES = 1


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartCleanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(
            runtime,
            translation_key="status",
            unique_id=f"{entry.entry_id}_status",
        ),
        LastJobSensor(
            runtime,
            translation_key="last_job",
            unique_id=f"{entry.entry_id}_last_job",
        ),
    ]
    entities.extend(
        RoomSensor(
            runtime,
            name=room.name,
            unique_id=f"{entry.entry_id}_room_{room.room_id}",
            room_id=room.room_id,
        )
        for room in runtime.config.rooms
    )
    async_add_entities(entities)


class _BaseSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: SmartCleanRuntime,
        *,
        unique_id: str,
        name: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        self._runtime = runtime
        # Setting _attr_name, even to None, overrides the translated name.
        if name is not None:
            self._attr_name = name
        self._attr_translation_key = translation_key
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info(runtime)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._runtime.signal, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class StatusSensor(_BaseSensor):
    """The one-line answer to "what is the robot doing?"."""

    @property
    def native_value(self) -> str:
        return self._runtime.status.sentence

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view: StatusView = self._runtime.status
        return {
            "phase": view.phase,
            "failed_gates": list(view.failed_gates),
            "job_rooms": list(view.job_rooms),
            "remaining": list(view.remaining),
            "next_attempt_at": _isoformat(view.next_attempt_at),
        }


class RoomSensor(_BaseSensor):
    """Per-room status, reason and attempt count."""

    def __init__(
        self,
        runtime: SmartCleanRuntime,
        *,
        name: str,
        unique_id: str,
        room_id: str,
    ) -> None:
        super().__init__(runtime, name=name, unique_id=unique_id)
        self._room_id = room_id

    def _view(self) -> RoomView | None:
        return next(
            (
                view
                for view in self._runtime.status.rooms
                if view.room_id == self._room_id
            ),
            None,
        )

    @property
    def native_value(self) -> str | None:
        view = self._view()
        return view.status if view is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self._view()
        if view is None:
            return {}
        return {
            "reason": view.reason,
            "last_cleaned": _isoformat(view.last_cleaned),
            "estimate_s": view.estimate_s,
            "attempts_today": view.attempts_today,
        }


class LastJobSensor(_BaseSensor):
    """The end state of the most recent job."""

    @property
    def native_value(self) -> str:
        result = self._runtime.state.last_result
        return result.end if result is not None else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        result = self._runtime.state.last_result
        if result is None:
            return {}
        return {
            "job_id": result.job_id,
            "origin": result.origin,
            "ended_at": _isoformat(result.ended_at),
            "done": list(result.done),
            "failed": list(result.failed),
            "dropped": list(result.dropped),
            "reason": result.reason,
        }
