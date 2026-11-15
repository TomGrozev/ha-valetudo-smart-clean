"""Switches for Valetudo Smart Clean."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartCleanConfigEntry, SmartCleanRuntime, device_info
from .core.models import Intent, SetCleanWhenOccupied, SetRoomEnabled, SetSmartClean

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartCleanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SwitchEntity] = [
        SmartCleanSwitch(
            runtime,
            translation_key="smart_clean",
            unique_id=f"{entry.entry_id}_smart_clean",
            is_on=lambda: runtime.state.smart_clean,
            intent=lambda on: SetSmartClean(on),
        ),
        SmartCleanSwitch(
            runtime,
            translation_key="clean_when_occupied",
            unique_id=f"{entry.entry_id}_clean_when_occupied",
            is_on=lambda: runtime.state.clean_when_occupied,
            intent=lambda on: SetCleanWhenOccupied(on),
        ),
    ]
    entities.extend(
        SmartCleanSwitch(
            runtime,
            name=room.name,
            unique_id=f"{entry.entry_id}_room_auto_{room.room_id}",
            is_on=lambda room_id=room.room_id: runtime.state.room_enabled.get(
                room_id, True
            ),
            intent=lambda on, room_id=room.room_id: SetRoomEnabled(room_id, on),
        )
        for room in runtime.config.rooms
    )
    async_add_entities(entities)


class SmartCleanSwitch(SwitchEntity):
    """A boolean the core owns; the switch only sends an intent."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: SmartCleanRuntime,
        *,
        unique_id: str,
        is_on: Callable[[], bool],
        intent: Callable[[bool], Intent],
        name: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._is_on = is_on
        self._intent = intent
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

    @property
    def is_on(self) -> bool:
        return self._is_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._runtime.async_intent(self._intent(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._runtime.async_intent(self._intent(False))
