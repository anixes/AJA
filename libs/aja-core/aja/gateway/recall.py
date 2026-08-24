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
                    min_score: float | None = None,
                    query_vector: list[float] | None = None) -> list[dict]:
    """Returns [{role, content, timestamp, score}] of semantically similar past exchanges.

    Uses get_embedding_service().embed(text) for the query vector unless
    ``query_vector`` is supplied (skips re-embedding; used by hybrid paths).
    vector_memory defaults to VectorMemory(table_name='mission_semantic') lazily
    constructed. Returns [] on any failure (embedding unavailable, empty store).
    """
    try:
        if vector_memory is None:
            from aja.memory.vector import VectorMemory
            vector_memory = VectorMemory(table_name="mission_semantic")

        if query_vector is None:
            from aja.embeddings.service import get_embedding_service

            query_vector = get_embedding_service().embed(query_text)
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


def keyword_recall(query_text: str, limit: int = 5) -> list[dict]:
    """FTS5 keyword search over the bi-temporal knowledge graph.

    Complements vector recall: exact-name/fact matches that embeddings
    rank poorly. Returns the same shape as :func:`semantic_recall`
    (rank-order, scores left 0.0 — fusion assigns RRF scores).
    """
    try:
        from aja.cognitive.temporal_graph import BiTemporalEntityGraph

        graph = BiTemporalEntityGraph()
        entities = graph.search_entities(query_text, limit=limit)
    except Exception as e:
        logger.debug("keyword_recall failed for %r: %s", query_text[:80], e)
        return []

    out = []
    for ent in entities:
        props = ent.properties or {}
        detail = props.get("value") or props.get("content") or props.get("summary") or ""
        content = f"{ent.name}: {detail}" if detail else ent.name
        stamp = ""
        if ent.valid_from:
            stamp = datetime.fromtimestamp(ent.valid_from, tz=timezone.utc).isoformat()
        out.append({
            "role": f"fact/{ent.entity_type}",
            "content": content,
            "timestamp": stamp,
            "score": 0.0,
        })
    return out


_RRF_K = 60


def _rrf_merge(*ranked_lists: list[dict], top_k: int = 5) -> list[dict]:
    """Reciprocal-rank-fusion of already-ranked recall lists, deduped by content."""
    scores: dict[str, dict] = {}
    for results in ranked_lists:
        for rank, entry in enumerate(results, start=1):
            key = " ".join(str(entry.get("content", "")).split())[:200]
            if not key:
                continue
            if key not in scores:
                scores[key] = dict(entry)
                scores[key]["score"] = 0.0
            scores[key]["score"] += 1.0 / (_RRF_K + rank)
    fused = sorted(scores.values(), key=lambda e: e["score"], reverse=True)
    return fused[:top_k]


def hybrid_recall(query_text: str, vector_memory=None, top_k: int = 5,
                  min_score: float | None = None,
                  temporal_hours: int = 24, journal_dir=None) -> tuple[list[dict], list[dict]]:
    """Dense + keyword hybrid recall.

    Fuses :func:`semantic_recall` (vector) and :func:`keyword_recall`
    (FTS5 over the bi-temporal graph) via reciprocal-rank fusion, then
    gathers :func:`time_recall` events. Returns ``(semantic, temporal)``
    in the shape :func:`format_recall_context` expects. Blocking; callers
    offload to a thread.
    """
    semantic = semantic_recall(
        query_text, vector_memory=vector_memory, top_k=top_k, min_score=min_score
    )
    keywords = keyword_recall(query_text, limit=top_k)
    fused = _rrf_merge(semantic, keywords, top_k=top_k) if (semantic or keywords) else []
    temporal = time_recall(hours_back=temporal_hours, journal_dir=journal_dir)
    return fused, temporal


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


def _time_recall_lancedb(cutoff: datetime) -> list[dict]:
    """Primary path: queries the LanceDB ``aja_runtime_events`` table for
    recall-relevant events within the window. Raises on any failure so the
    caller can fall back to the JSONL scan. Blocking; callers offload."""
    from aja.memory.secretary import get_aja_memory

    memory = get_aja_memory()
    table = memory.db.open_table("aja_runtime_events")
    rows = table.search().limit(1000).to_list()

    hits = []
    for row in rows:
        if row.get("kind") not in _RECALL_EVENT_TYPES:
            continue
        ts = _parse_event_timestamp(row.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        event = {}
        raw_meta = row.get("metadata_json")
        if raw_meta:
            try:
                event = json.loads(raw_meta)
            except (TypeError, ValueError):
                event = {}
        if not isinstance(event, dict) or not event:
            event = {
                "tool": row.get("target", ""),
                "success": row.get("status") != "failed",
            }
        hits.append({
            "event_type": row.get("kind"),
            "timestamp": row.get("timestamp", ""),
            "summary": _event_summary(event),
            "_sort_ts": ts,
        })
    return hits


def _time_recall_journal_scan(cutoff: datetime, journal_dir=None) -> list[dict]:
    """Fallback path: full-scans DATA_DIR/missions/mission_*.jsonl journals."""
    try:
        directory = Path(journal_dir) if journal_dir else Path(__import__(
            "aja.config", fromlist=["DATA_DIR"]).DATA_DIR) / "missions"
    except Exception as e:
        logger.debug("time_recall could not resolve journal dir: %s", e)
        return []

    if not directory.is_dir():
        return []

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
    return hits


def time_recall(hours_back: int = 24, journal_dir=None) -> list[dict]:
    """Returns recall-relevant runtime events (TOOL_COMPLETED/MISSION_COMPLETED
    within the last N hours) as [{event_type, timestamp, summary}] sorted
    newest-first. Primary source is the LanceDB ``aja_runtime_events`` table;
    falls back to a JSONL mission-journal scan when the table is missing,
    empty, or the query fails. Blocking; callers offload.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    # An explicitly-passed journal_dir signals intent to scan that journal
    # (test harnesses, targeted tooling) — honor it as the primary source.
    if journal_dir is not None:
        hits = _time_recall_journal_scan(cutoff, journal_dir)
    else:
        try:
            hits = _time_recall_lancedb(cutoff)
        except Exception as e:
            logger.debug("time_recall LanceDB path unavailable (%s); using JSONL scan", e)
            hits = []
        if not hits:
            hits = _time_recall_journal_scan(cutoff, journal_dir)

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
