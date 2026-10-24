"""Diagnostics for Sdui: shape of the API responses, without personal data."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import SduiConfigEntry
from .const import FETCH_DAYS

# Only values of these lesson fields are included, everything else as key list
LESSON_VALUE_KEYS = ("begins_at", "ends_at", "kind")


def _shape(value: Any) -> Any:
    """Type/keys of a value without its content."""
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(value)}
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    return {"type": type(value).__name__}


async def _probe(
    client, path: str, params: dict | None = None, lessons_key: str | None = None
) -> dict[str, Any]:
    """Call an API path and describe the response."""
    try:
        raw = await client.fetch_raw(path, params)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    data = raw.get("data") if isinstance(raw, dict) else None
    result: dict[str, Any] = {"response": _shape(raw), "data": _shape(data)}
    items = data.get(lessons_key) if lessons_key and isinstance(data, dict) else data
    if isinstance(items, list):
        result["items"] = len(items)
        if items and isinstance(items[0], dict):
            result["first_item_keys"] = sorted(items[0])
            if lessons_key:
                result["first_item"] = {k: items[0].get(k) for k in LESSON_VALUE_KEYS}
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SduiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client
    today = dt_util.now().date()
    ranges = {
        "fetch_range": (today, today + timedelta(days=FETCH_DAYS)),
        "past_week": (today - timedelta(days=7), today),
    }

    students: dict[str, Any] = {}
    for user_id in coordinator.students:
        timetable = (coordinator.data or {}).get(user_id)
        students[user_id] = {
            "coordinator_lessons": len(timetable.lessons) if timetable else None,
            "coordinator_days": sorted(d.isoformat() for d in timetable.by_date)
            if timetable
            else None,
            **{
                name: {
                    "range": [start.isoformat(), end.isoformat()],
                    **await _probe(
                        client,
                        f"/timetables/users/{user_id}/timetable",
                        {"begins_at": start.isoformat(), "ends_at": end.isoformat()},
                        lessons_key="lessons",
                    ),
                }
                for name, (start, end) in ranges.items()
            },
        }

    return {
        "last_update_success": coordinator.last_update_success,
        "timezone": str(dt_util.get_default_time_zone()),
        "students": students,
        "users_self": await _probe(client, "/users/self"),
        "users_self_family": await _probe(client, "/users/self/family"),
    }
