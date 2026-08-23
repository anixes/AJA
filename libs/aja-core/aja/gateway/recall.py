"""Recall engine: semantic + temporal recall over offloaded chat history
and mission journals, formatted for injection as a system-role context block."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_RECALL_EVENT_TYPES = ("TOOL_COMPLETED", "MISSION_COMPLETED")
_MAX_CONTEXT_CHARS = 2000


def semantic_recall(query_text: str, vector_memory=None, top_k: int = 5,
                    min_score: float | None = None) -> list[dict]:
    """Returns [{role, content, timestamp, score}] of semantically similar past exchanges.

    Uses get_text_embedding(text) from aja.memory.territory for the query vector.
    vector_memory defaults to VectorMemory(table_name='mission_semantic') lazily
    constructed. Returns [] on any failure (embedding unavailable, empty store).
    """
    try:
        from aja.memory.territory import get_text_embedding

        if vector_memory is None:
            from aja.memory.vector import VectorMemory
            vector_memory = VectorMemory(table_name="mission_semantic")

        query_vector = get_text_embedding(query_text)
        raw_results = vector_memory.search(query_vector, top_k) or []
    except Exception as e:
        logger.debug("semantic_recall failed for %r: %s", query_text[:80], e)
        return []

    normalized = []
    for row in raw_results:
        try:
            meta = row.get("metadata") or {}
            distance = row.get("score", 0) or 0
            similarity = 1.0 / (1.0 + float(distance))
            if min_score is not None and similarity < min_score:
                continue
            normalized.append({
                "role": meta.get("role", "unknown"),
                "content": meta.get("content", "") or row.get("text", ""),
                "timestamp": meta.get("timestamp", ""),
                "score": similarity,
            })
        except Exception as e:
            logger.debug("Skipping malformed recall row: %s", e)
    return normalized


def _parse_event_timestamp(value) -> datetime | None:
    """Parses an ISO-8601 timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_summary(event: dict) -> str:
    if event.get("event_type") == "MISSION_COMPLETED":
        return event.get("result_summary") or event.get("goal") or "Mission completed"
    tool = event.get("tool") or event.get("tool_name") or "unknown tool"
    status = "ok" if event.get("success", True) else "failed"
    return f"{tool} ({status})"


def time_recall(hours_back: int = 24, journal_dir=None) -> list[dict]:
    """Scans mission journals for TOOL_COMPLETED/MISSION_COMPLETED events within
    the last N hours. Returns [{event_type, timestamp, summary}] sorted
    newest-first. journal_dir defaults to DATA_DIR / 'missions'. Returns [] on
    missing dir.
    """
    try:
        directory = Path(journal_dir) if journal_dir else Path(__import__(
            "aja.config", fromlist=["DATA_DIR"]).DATA_DIR) / "missions"
    except Exception as e:
        logger.debug("time_recall could not resolve journal dir: %s", e)
        return []

    if not directory.is_dir():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    hits = []
    for path in sorted(directory.glob("mission_*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn-write tolerant
                    if not isinstance(event, dict):
                        continue
                    if event.get("event_type") not in _RECALL_EVENT_TYPES:
                        continue
                    ts = _parse_event_timestamp(event.get("timestamp"))
                    if ts is None or ts < cutoff:
                        continue
                    hits.append({
                        "event_type": event.get("event_type"),
                        "timestamp": event.get("timestamp", ""),
                        "summary": _event_summary(event),
                        "_sort_ts": ts,
                    })
        except OSError as e:
            logger.debug("Could not read journal %s: %s", path, e)

    hits.sort(key=lambda h: h["_sort_ts"], reverse=True)
    for hit in hits:
        hit.pop("_sort_ts", None)
    return hits


def format_recall_context(semantic: list[dict], temporal: list[dict]) -> str:
    """Formats recall results into a single markdown block suitable for
    injection as a system-role message. Empty input -> '' (callers check
    truthiness). Total output capped at ~2000 chars; oldest entries are
    truncated first.
    """
    if not semantic and not temporal:
        return ""

    def _semantic_line(entry: dict) -> str:
        role = entry.get("role", "unknown")
        content = " ".join(str(entry.get("content", "")).split())
        ts = entry.get("timestamp", "")
        stamp = f" @ {ts}" if ts else ""
        return f"- [{role}{stamp}] {content}"

    def _temporal_line(entry: dict) -> str:
        etype = entry.get("event_type", "EVENT")
        summary = " ".join(str(entry.get("summary", "")).split())
        ts = entry.get("timestamp", "")
        stamp = f" @ {ts}" if ts else ""
        return f"- [{etype}{stamp}] {summary}"

    sections = []
    if semantic:
        sections.append(("## Previously discussed",
                         [_semantic_line(e) for e in semantic]))
    if temporal:
        sections.append(("## Recent activity",
                         [_temporal_line(e) for e in temporal]))

    truncated = False
    while sections:
        total = sum(len(header) + 2 + sum(len(l) + 1 for l in lines)
                    for header, lines in sections)
        if total <= _MAX_CONTEXT_CHARS:
            break
        # Drop the last (oldest) entry from the section with the most lines.
        target = max(sections, key=lambda s: len(s[1]))
        header, lines = target
        if not lines:
            sections.remove(target)
            continue
        lines.pop()
        truncated = True
        if not lines:
            sections.remove(target)
    if not sections:
        return ""

    parts = []
    for header, lines in sections:
        parts.append(header)
        parts.extend(lines)
    if truncated:
        parts.append("_(older recalled context truncated)_")
    return "\n".join(parts)
