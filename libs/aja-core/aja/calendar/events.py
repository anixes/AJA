"""Google Calendar event operations.

All google imports stay inside :mod:`aja.calendar.auth`; this module works
purely against the authorized service object, so tests can inject a mock.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from aja.calendar.auth import get_service

logger = logging.getLogger(__name__)


def _normalize_event(item: Dict[str, Any]) -> Dict[str, Any]:
    start = item.get("start") or {}
    end = item.get("end") or {}
    return {
        "id": item.get("id"),
        "title": item.get("summary", ""),
        "start_iso": start.get("dateTime") or start.get("date"),
        "end_iso": end.get("dateTime") or end.get("date"),
        "location": item.get("location", ""),
        "description": item.get("description", ""),
        "html_link": item.get("htmlLink", ""),
    }


def list_events(
    time_min_iso: str,
    time_max_iso: str,
    calendar_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """List events in a window across calendars (primary calendar by default).

    Returns normalized dicts with keys:
    id, title, start_iso, end_iso, location, description, html_link.
    """
    if calendar_ids is None:
        calendar_ids = ["primary"]

    service = get_service()
    results: List[Dict[str, Any]] = []
    for calendar_id in calendar_ids:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        for item in response.get("items", []):
            results.append(_normalize_event(item))
    return results


def create_event(
    title: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    calendar_id: str = "primary",
) -> Dict[str, Any]:
    """Create an event and return it in the same normalized shape."""
    service = get_service()
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    created = (
        service.events().insert(calendarId=calendar_id, body=body).execute()
    )
    return _normalize_event(created)
