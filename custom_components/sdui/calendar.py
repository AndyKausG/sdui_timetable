"""Calendar platform for Sdui."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SduiConfigEntry
from .const import KIND_SUBSTITUTION
from .coordinator import is_cancelled, lesson_end, lesson_start
from .entity import SduiStudentEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SduiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one Sdui calendar per selected student."""
    coordinator = entry.runtime_data
    async_add_entities(
        SduiCalendar(coordinator, user_id) for user_id in coordinator.students
    )


def _lesson_to_event(lesson: dict) -> CalendarEvent | None:
    """Convert a lesson dict to a CalendarEvent."""
    start = lesson_start(lesson)
    end = lesson_end(lesson)
    if start is None or end is None:
        return None

    subject = lesson.get("subject") or "Unknown"
    kind_label = lesson.get("displayname_kind", "")

    if is_cancelled(lesson):
        summary = f"[{(kind_label or 'Entfall').upper()}] {subject}"
    elif lesson.get("kind") == KIND_SUBSTITUTION:
        summary = f"[{(kind_label or 'Vertretung').upper()}] {subject}"
    else:
        summary = subject

    teachers = ", ".join(lesson.get("teachers", []))
    rooms = ", ".join(lesson.get("rooms", []))
    comment = lesson.get("comment", "")
    hour = lesson.get("hour", "")

    desc_parts = []
    # Some schools send a clock emoji instead of the lesson number
    if any(c.isdigit() for c in str(hour)):
        desc_parts.append(f"Stunde: {hour}")
    if teachers:
        desc_parts.append(f"Lehrkraft: {teachers}")
    # The room is the event location, not repeated here
    if comment:
        desc_parts.append(f"Hinweis: {comment}")

    return CalendarEvent(
        start=start,
        end=end,
        summary=summary,
        description="\n".join(desc_parts) if desc_parts else None,
        location=rooms or None,
    )


class SduiCalendar(SduiStudentEntity, CalendarEntity):
    """Calendar with the timetable of one student."""

    _attr_translation_key = "timetable"
    _attr_icon = "mdi:school"

    def __init__(self, coordinator, user_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator, user_id, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next lesson."""
        timetable = self.timetable
        lesson = timetable.next_lesson() if timetable else None
        return _lesson_to_event(lesson) if lesson else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return lessons within a time range."""
        timetable = self.timetable
        if timetable is None:
            return []
        start_ts = start_date.timestamp()
        end_ts = end_date.timestamp()
        events = []
        for lesson in timetable.lessons:
            if (lesson.get("ends_at") or 0) < start_ts or (lesson.get("begins_at") or 0) > end_ts:
                continue
            if (evt := _lesson_to_event(lesson)) is not None:
                events.append(evt)
        return events
