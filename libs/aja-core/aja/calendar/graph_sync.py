"""Sync Google Calendar events into the bi-temporal knowledge graph.

Each upcoming event becomes an entity of type ``calendar_event`` named
``[cal] <title>`` whose validity window is the event's real-world start and
end times (valid_from = start epoch, valid_to = end epoch). Re-syncs
supersede cleanly via BiTemporalEntityGraph's non-destructive upsert.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aja.calendar.events import list_events

logger = logging.getLogger(__name__)

ENTITY_TYPE = "calendar_event"


def _parse_iso(value: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 string into epoch seconds; naive values assume UTC."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse calendar timestamp: %r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _default_graph():
    from aja.cognitive.temporal_graph import BiTemporalEntityGraph

    return BiTemporalEntityGraph()


def sync_to_graph(graph=None, days_ahead: int = 7) -> List[str]:
    """Pull upcoming events and upsert each into the bi-temporal graph.

    Accepts an injected graph for tests; constructs a default
    BiTemporalEntityGraph lazily when omitted. Returns the synced names.
    """
    if graph is None:
        graph = _default_graph()

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=int(days_ahead))).isoformat()

    synced: List[str] = []
    for event in list_events(time_min, time_max):
        title = (event.get("title") or "").strip()
        if not title:
            continue
        start_epoch = _parse_iso(event.get("start_iso"))
        if start_epoch is None:
            continue
        end_epoch = _parse_iso(event.get("end_iso"))

        name = f"[cal] {title}"
        properties: Dict[str, Any] = {
            "title": title,
            "location": event.get("location", ""),
            "html_link": event.get("html_link", ""),
            "description": event.get("description", ""),
            "start_iso": event.get("start_iso"),
            "end_iso": event.get("end_iso"),
        }
        graph.upsert_entity(ENTITY_TYPE, name, properties, valid_from=start_epoch)
        # valid_to is set to the event's end epoch so the fact "this event
        # exists" is only valid across its real-world window.
        if end_epoch is not None:
            graph.invalidate_entity(ENTITY_TYPE, name, invalid_at=end_epoch)
        synced.append(name)

    logger.info("Synced %d calendar events into the knowledge graph.", len(synced))
    return synced


def events_between(
    start_iso: str,
    end_iso: str,
    graph=None,
) -> List[Dict[str, Any]]:
    """Query the graph for calendar_event entities overlapping [start, end).

    Overlap semantics are interval-based (event.valid_from < end AND
    event.valid_to > start), independent of wall-clock "now".
    """
    if graph is None:
        graph = _default_graph()

    start_epoch = _parse_iso(start_iso)
    end_epoch = _parse_iso(end_iso)
    if start_epoch is None or end_epoch is None:
        raise ValueError("events_between requires parseable ISO timestamps.")

    rows_out: List[Dict[str, Any]] = []
    conn = sqlite3.connect(str(graph.db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            """
            SELECT entity_id, name, properties_json, valid_from, valid_to
            FROM entities
            WHERE entity_type = ?
              AND valid_from < ?
              AND (valid_to IS NULL OR valid_to > ?)
            ORDER BY valid_from ASC
            """,
            (ENTITY_TYPE, end_epoch, start_epoch),
        )
        for row in cursor.fetchall():
            try:
                properties = json.loads(row["properties_json"])
            except (TypeError, ValueError):
                properties = {}
            rows_out.append(
                {
                    "entity_id": row["entity_id"],
                    "name": row["name"],
                    "properties": properties,
                    "start_epoch": row["valid_from"],
                    "end_epoch": row["valid_to"],
                }
            )
    finally:
        conn.close()
    return rows_out
