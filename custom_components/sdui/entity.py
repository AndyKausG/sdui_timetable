"""Base entity for Sdui."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SduiCoordinator, StudentTimetable


class SduiStudentEntity(CoordinatorEntity[SduiCoordinator]):
    """Entity belonging to one student (one device per student)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SduiCoordinator, user_id: str, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.user_id = user_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{user_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{user_id}")},
            name=f"Sdui {coordinator.students.get(user_id, user_id)}",
            manufacturer="Sdui",
            model="Stundenplan",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://sdui.app/timetable/users/{user_id}",
        )

    @property
    def timetable(self) -> StudentTimetable | None:
        """Timetable of this student from the last update."""
        return (self.coordinator.data or {}).get(self.user_id)
