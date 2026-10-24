"""DataUpdateCoordinator for Sdui."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SduiApiClient, SduiApiError, SduiAuthError
from .const import (
    DOMAIN,
    EVENT_CHANGE,
    FETCH_DAYS,
    PAST_DAYS,
    KIND_SUBSTITUTION,
    KINDS_CANCELLED,
    SCAN_INTERVAL_MINUTES,
)

if TYPE_CHECKING:
    from . import SduiConfigEntry

_LOGGER = logging.getLogger(__name__)


def _parse_lesson(lesson: dict) -> dict:
    """Normalize a raw API lesson into a flat dict."""
    try:
        # Safely access nested dictionaries with null checks
        course = lesson.get("course") or {}
        meta_course = course.get("meta") if isinstance(course, dict) else {}
        meta_lesson = lesson.get("meta") or {}

        # Safely process teachers array
        teachers = lesson.get("teachers") or []
        teacher_names = []
        if isinstance(teachers, list):
            for t in teachers:
                if isinstance(t, dict):
                    teacher_names.append(t.get("shortcut", t.get("name", "")))

        # Safely process bookables array
        bookables = lesson.get("bookables") or []
        rooms = []
        if isinstance(bookables, list):
            for b in bookables:
                if isinstance(b, dict):
                    rooms.append(b.get("name", b.get("shortcut", "")))

        # Safely process grades array
        grades = lesson.get("grades") or []
        grade_names = []
        if isinstance(grades, list):
            for g in grades:
                if isinstance(g, dict):
                    grade_names.append(g.get("name", ""))

        kind = lesson.get("kind")  # None, "SUBSTITUTION", "CANCLED"
        
        # Safely process referenced lessons with recursion protection
        referenced = lesson.get("referenced_target_lessons") or []
        referenced_lessons = []
        if isinstance(referenced, list):
            for r in referenced:
                if isinstance(r, dict):
                    try:
                        referenced_lessons.append(_parse_lesson(r))
                    except Exception as ref_exc:  # noqa: BLE001
                        _LOGGER.warning("Failed to parse referenced lesson: %s", ref_exc)

        return {
            "id": lesson.get("id"),
            "begins_at": lesson.get("begins_at"),
            "ends_at": lesson.get("ends_at"),
            "subject": (meta_lesson.get("displayname") if isinstance(meta_lesson, dict) else None) or 
                      (meta_course.get("displayname") if isinstance(meta_course, dict) else "") or "",
            "shortname": (meta_lesson.get("shortname") if isinstance(meta_lesson, dict) else None) or 
                        (meta_course.get("shortname") if isinstance(meta_course, dict) else "") or "",
            "teachers": teacher_names,
            "rooms": rooms,
            "grades": grade_names,
            "hour": meta_lesson.get("displayname_hour", "") if isinstance(meta_lesson, dict) else "",
            "kind": kind,
            "comment": lesson.get("comment", ""),
            "displayname_kind": meta_lesson.get("displayname_kind", "") if isinstance(meta_lesson, dict) else "",
            "referenced_lessons": referenced_lessons,
        }
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to parse lesson %s: %s", lesson.get("id"), exc, exc_info=True)
        _LOGGER.debug("Problematic lesson data: %s", lesson)
        # Return a minimal valid lesson structure to prevent total failure
        return {
            "id": lesson.get("id"),
            "begins_at": lesson.get("begins_at"),
            "ends_at": lesson.get("ends_at"),
            "subject": "Unknown",
            "shortname": "",
            "teachers": [],
            "rooms": [],
            "grades": [],
            "hour": "",
            "kind": lesson.get("kind"),
            "comment": lesson.get("comment", ""),
            "displayname_kind": "",
            "referenced_lessons": [],
        }



def is_cancelled(lesson: dict) -> bool:
    """Return True if the lesson is cancelled."""
    return lesson.get("kind") in KINDS_CANCELLED


def is_change(lesson: dict) -> bool:
    """Return True if the lesson is a substitution or cancellation."""
    return lesson.get("kind") == KIND_SUBSTITUTION or is_cancelled(lesson)


def lesson_start(lesson: dict) -> datetime | None:
    """Start of a lesson as aware local datetime."""
    ts = lesson.get("begins_at")
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)) if ts else None


def lesson_end(lesson: dict) -> datetime | None:
    """End of a lesson as aware local datetime."""
    ts = lesson.get("ends_at")
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)) if ts else None


def _change_key(lesson: dict) -> tuple:
    """Identity of a substitution/cancellation for change detection."""
    return (
        lesson.get("id"),
        lesson.get("begins_at"),
        lesson.get("kind"),
        lesson.get("comment"),
        tuple(lesson.get("teachers", [])),
        tuple(lesson.get("rooms", [])),
    )


class StudentTimetable:
    """Timetable of one student with helpers for the entities."""

    def __init__(self, lessons: list[dict]) -> None:
        """Group lessons by local date."""
        self.lessons = sorted(lessons, key=lambda x: x.get("begins_at") or 0)
        self.by_date: dict[date, list[dict]] = {}
        for lesson in self.lessons:
            start = lesson_start(lesson)
            if start is not None:
                self.by_date.setdefault(start.date(), []).append(lesson)

    def day(self, day: date | None) -> list[dict]:
        """All lessons of a day (including cancelled ones)."""
        return self.by_date.get(day, []) if day else []

    def held(self, day: date | None) -> list[dict]:
        """Lessons of a day that take place (substitutions included)."""
        return [l for l in self.day(day) if not is_cancelled(l)]

    def changes(self, day: date | None) -> list[dict]:
        """Substitutions and cancellations of a day."""
        return [l for l in self.day(day) if is_change(l)]

    def next_school_day(self) -> date | None:
        """First day after today with lessons."""
        today = dt_util.now().date()
        return next((d for d in sorted(self.by_date) if d > today), None)

    def school_start(self, day: date | None) -> datetime | None:
        """Begin of the first lesson that takes place."""
        held = self.held(day)
        return lesson_start(held[0]) if held else None

    def school_end(self, day: date | None) -> datetime | None:
        """End of the last lesson that takes place."""
        held = self.held(day)
        return max((lesson_end(l) for l in held if l.get("ends_at")), default=None)

    def next_lesson(self) -> dict | None:
        """Currently running or next lesson that takes place."""
        now_ts = dt_util.utcnow().timestamp()
        return next(
            (
                l for l in self.lessons
                if (l.get("ends_at") or 0) >= now_ts and not is_cancelled(l)
            ),
            None,
        )


class SduiCoordinator(DataUpdateCoordinator[dict[str, StudentTimetable]]):
    """Fetches the timetables of all selected students of one account."""

    config_entry: SduiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SduiConfigEntry,
        client: SduiApiClient,
        students: dict[str, str],
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MINUTES),
        )
        self.client = client
        self.students = students
        self._known_changes: dict[str, set[tuple]] | None = None

    async def _async_update_data(self) -> dict[str, StudentTimetable]:
        """Fetch the timetable of every student (last week and next days)."""
        today = dt_util.now().date()
        start = today - timedelta(days=PAST_DAYS)
        end = today + timedelta(days=FETCH_DAYS)
        data: dict[str, StudentTimetable] = {}

        try:
            for user_id in self.students:
                raw = await self.client.fetch_timetable(
                    user_id, start.isoformat(), end.isoformat()
                )
                _LOGGER.debug("Student %s: %d lessons received", user_id, len(raw))
                data[user_id] = StudentTimetable([_parse_lesson(l) for l in raw])
        except SduiAuthError as exc:
            # Triggers the re-authentication flow in the UI
            raise ConfigEntryAuthFailed(f"Authentication error: {exc}") from exc
        except SduiApiError as exc:
            raise UpdateFailed(f"API error: {exc}") from exc

        self._fire_change_events(data)
        return data

    def _fire_change_events(self, data: dict[str, StudentTimetable]) -> None:
        """Fire an event for every substitution/cancellation not seen before."""
        now_ts = dt_util.utcnow().timestamp()
        current = {
            user_id: {
                _change_key(l): l
                for l in timetable.lessons
                if is_change(l) and (l.get("ends_at") or 0) >= now_ts
            }
            for user_id, timetable in data.items()
        }
        known, self._known_changes = self._known_changes, {
            user_id: set(changes) for user_id, changes in current.items()
        }
        # First update after start: only remember, do not flood with events
        if known is None:
            return

        for user_id, changes in current.items():
            for key, lesson in changes.items():
                if key in known.get(user_id, set()):
                    continue
                self.hass.bus.async_fire(EVENT_CHANGE, event_data(self, user_id, lesson))


def event_data(coordinator: SduiCoordinator, user_id: str, lesson: dict) -> dict[str, Any]:
    """Payload of a change event."""
    start = lesson_start(lesson)
    end = lesson_end(lesson)
    return {
        "student_id": user_id,
        "student": coordinator.students.get(user_id),
        "type": "cancellation" if is_cancelled(lesson) else "substitution",
        "date": start.date().isoformat() if start else None,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "hour": lesson.get("hour"),
        "subject": lesson.get("subject"),
        "kind_label": lesson.get("displayname_kind"),
        "teachers": lesson.get("teachers", []),
        "rooms": lesson.get("rooms", []),
        "comment": lesson.get("comment"),
    }
