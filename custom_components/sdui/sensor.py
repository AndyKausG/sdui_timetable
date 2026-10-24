"""Sensor platform for Sdui."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SduiConfigEntry
from .coordinator import StudentTimetable, lesson_end, lesson_start
from .entity import SduiStudentEntity


def lesson_attrs(lesson: dict) -> dict[str, Any]:
    """Convert a lesson dict to a simplified attribute dict."""
    start = lesson_start(lesson)
    end = lesson_end(lesson)
    return {
        "subject": lesson.get("subject"),
        "shortname": lesson.get("shortname"),
        "teachers": lesson.get("teachers", []),
        "rooms": lesson.get("rooms", []),
        "grades": lesson.get("grades", []),
        "hour": lesson.get("hour"),
        "kind": lesson.get("kind"),
        "kind_label": lesson.get("displayname_kind"),
        "comment": lesson.get("comment"),
        "date": start.date().isoformat() if start else None,
        "start": start.strftime("%H:%M") if start else None,
        "end": end.strftime("%H:%M") if end else None,
    }


def _today() -> date:
    return dt_util.now().date()


@dataclass(frozen=True, kw_only=True)
class SduiSensorDescription(SensorEntityDescription):
    """Describes a Sdui sensor."""

    value_fn: Callable[[StudentTimetable], Any]
    attr_fn: Callable[[StudentTimetable], dict[str, Any]] | None = None


def _next_lesson_attrs(t: StudentTimetable) -> dict[str, Any]:
    lesson = t.next_lesson()
    return lesson_attrs(lesson) if lesson else {}


def _day_attrs(t: StudentTimetable, day: date | None) -> dict[str, Any]:
    return {
        "date": day.isoformat() if day else None,
        "lessons": [lesson_attrs(l) for l in t.day(day)],
    }


def _changes_attrs(t: StudentTimetable, day: date | None) -> dict[str, Any]:
    return {
        "date": day.isoformat() if day else None,
        "changes": [lesson_attrs(l) for l in t.changes(day)],
    }


SENSORS: tuple[SduiSensorDescription, ...] = (
    SduiSensorDescription(
        key="next_lesson",
        translation_key="next_lesson",
        icon="mdi:school",
        value_fn=lambda t: (t.next_lesson() or {}).get("subject"),
        attr_fn=_next_lesson_attrs,
    ),
    SduiSensorDescription(
        key="lessons_today",
        translation_key="lessons_today",
        icon="mdi:calendar-today",
        value_fn=lambda t: len(t.held(_today())),
        attr_fn=lambda t: _day_attrs(t, _today()),
    ),
    SduiSensorDescription(
        key="changes_today",
        translation_key="changes_today",
        icon="mdi:swap-horizontal",
        value_fn=lambda t: len(t.changes(_today())),
        attr_fn=lambda t: _changes_attrs(t, _today()),
    ),
    SduiSensorDescription(
        key="next_school_day",
        translation_key="next_school_day",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda t: t.next_school_day(),
        attr_fn=lambda t: _day_attrs(t, t.next_school_day()),
    ),
    SduiSensorDescription(
        key="changes_next_day",
        translation_key="changes_next_day",
        icon="mdi:swap-horizontal-bold",
        value_fn=lambda t: len(t.changes(t.next_school_day())),
        attr_fn=lambda t: _changes_attrs(t, t.next_school_day()),
    ),
    SduiSensorDescription(
        key="school_start_today",
        translation_key="school_start_today",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda t: t.school_start(_today()),
    ),
    SduiSensorDescription(
        key="school_end_today",
        translation_key="school_end_today",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda t: t.school_end(_today()),
    ),
    SduiSensorDescription(
        key="school_start_next_day",
        translation_key="school_start_next_day",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda t: t.school_start(t.next_school_day()),
    ),
    SduiSensorDescription(
        key="school_end_next_day",
        translation_key="school_end_next_day",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda t: t.school_end(t.next_school_day()),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SduiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sdui sensors for every selected student."""
    coordinator = entry.runtime_data
    async_add_entities(
        SduiSensor(coordinator, user_id, description)
        for user_id in coordinator.students
        for description in SENSORS
    )


class SduiSensor(SduiStudentEntity, SensorEntity):
    """Sensor of one student."""

    entity_description: SduiSensorDescription

    def __init__(self, coordinator, user_id: str, description: SduiSensorDescription) -> None:
        """Initialize."""
        super().__init__(coordinator, user_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | date | datetime | None:
        """Return the sensor value."""
        timetable = self.timetable
        return self.entity_description.value_fn(timetable) if timetable else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details."""
        timetable = self.timetable
        if timetable is None or self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(timetable)
