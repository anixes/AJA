"""Shared cross-platform approval resolution engine.

Both Telegram and Discord adapters delegate their approve/reject button
handling here so that authorization-adjacent state transitions produce
IDENTICAL journal outcomes regardless of platform:

- Mission lookup via aja.memory.secretary.get_aja_memory()
- Approval expiry enforcement (metadata approval_expires_at/expires_at)
- Idempotency guard against terminal/active statuses
- update_mission status transition (ACTIVE / REJECTED)
- NODE_APPROVED / NODE_REJECTED journal rows in aja_runtime_events

Authorization is intentionally delegated to each platform adapter (they own
their own allowlist semantics) BEFORE calling resolve_approval().
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_TERMINAL_OR_ACTIVE_STATUSES = {"ACTIVE", "DONE", "FAILED", "REJECTED"}

# Per-mission in-process claim locks: serialize approve/reject transitions so
# concurrent callbacks (double-click, retry redelivery) cannot both observe a
# pre-terminal status. The lock is held across the status flip AND the side
# effects; the status re-check inside the lock closes the TOCTOU window within
# a single process. Cross-process callers (multi-process aja serve + workers)
# remain guarded only by the terminal-status check — LanceDB offers no CAS.
#
# No guard lock around setdefault: it is atomic under the GIL and contains no
# await point, and a module-level asyncio.Lock would bind to whichever event
# loop first awaited it (breaking every later fresh-loop caller).
_mission_locks: Dict[str, asyncio.Lock] = {}


def _mission_lock(mission_id: str) -> asyncio.Lock:
    return _mission_locks.setdefault(mission_id, asyncio.Lock())


def _parse_expiry(value) -> Optional[datetime]:
    """Parses an ISO expiry stamp with tz-awareness.

    Naive stamps are assumed UTC. Returns None when unparseable — the caller
    treats that as expired (fail-closed, matching bridge.approval_is_expired).
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _journal_event(kind: str, mission_id: str, message: str, status: str) -> dict:
    return {
        "event_id": uuid.uuid4().hex[:8],
        "kind": kind,
        "target": mission_id,
        "status": status,
        "message": message,
        "command": "",
        "metadata_json": "{}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def resolve_approval(
    platform: str,
    user_id: str,
    mission_id: str,
    action: str = "approve",
) -> Tuple[bool, str]:
    """Resolve an approve/reject decision for ``mission_id``.

    Returns ``(handled, message_text)``. When ``handled`` is False the
    returned text explains why nothing changed (missing/expired/handled);
    callers should surface it verbatim. When True the side effects
    (status transition + journal event) were applied and the text is the
    confirmation outcome.

    Atomicity: a per-mission lock is held for the whole check-then-act
    transition, and the terminal/active status guard re-runs INSIDE the lock,
    so concurrent duplicate callbacks resolve exactly one side effect.
    """
    async with _mission_lock(mission_id):
        return await _resolve_claimed(platform, user_id, mission_id, action)


async def _resolve_claimed(
    platform: str,
    user_id: str,
    mission_id: str,
    action: str,
) -> Tuple[bool, str]:
    from aja.memory.secretary import get_aja_memory

    memory = get_aja_memory()
    mission = await asyncio.to_thread(memory.get_mission, mission_id)
    if not mission:
        return (
            False,
            f"\u2139\ufe0f Mission {mission_id} no longer exists or was already handled.",
        )

    status = str(mission.get("status", "")).upper()
    metadata_raw = mission.get("metadata_json") or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except Exception:
        metadata = {}

    expires_at = metadata.get("approval_expires_at") or metadata.get("expires_at")
    if expires_at:
        parsed_expires_at = _parse_expiry(expires_at)
        if parsed_expires_at is None:
            # Fail closed: an unparseable expiry must never leave buttons live.
            logger.warning(
                "Unparseable approval expiry %r for %s — treating as expired",
                expires_at,
                mission_id,
            )
            parsed_expires_at = datetime.min.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > parsed_expires_at:
            return False, f"\u23f3 Mission {mission_id} approval has expired."

    if status in _TERMINAL_OR_ACTIVE_STATUSES:
        return (
            False,
            f"\u2139\ufe0f Mission {mission_id} already handled (status: {status}).",
        )

    if action == "approve":
        try:
            # Update mission status to ACTIVE to signal GoalEngine to resume
            await asyncio.to_thread(memory.update_mission, mission_id, {"status": "ACTIVE"})
            table = memory.db.open_table("aja_runtime_events")
            await asyncio.to_thread(
                table.add,
                [
                    _journal_event(
                        "NODE_APPROVED",
                        mission_id,
                        f"User approved mission {mission_id}",
                        "SUCCESS",
                    )
                ],
            )
        except Exception as e:
            logger.exception("Approval transition failed for %s", mission_id)
            await _rollback_status(memory, mission_id, status)
            return False, f"\u26a0\ufe0f Mission {mission_id} approval failed: {e}"
        logger.info(
            "[%s] user %s approved mission %s", platform, user_id, mission_id
        )
        return True, f"\u2705 Mission {mission_id} Approved."

    # Reject
    try:
        await asyncio.to_thread(memory.update_mission, mission_id, {"status": "REJECTED"})
        table = memory.db.open_table("aja_runtime_events")
        await asyncio.to_thread(
            table.add,
            [
                _journal_event(
                    "NODE_REJECTED",
                    mission_id,
                    f"User rejected mission {mission_id}",
                    "ERROR",
                )
            ],
        )
    except Exception as e:
        logger.exception("Rejection transition failed for %s", mission_id)
        await _rollback_status(memory, mission_id, status)
        return False, f"\u26a0\ufe0f Mission {mission_id} rejection failed: {e}"
    logger.info("[%s] user %s rejected mission %s", platform, user_id, mission_id)
    return True, f"\u274c Mission {mission_id} Rejected."


async def _rollback_status(memory, mission_id: str, previous_status: str) -> None:
    """Best-effort restore of the prior status after a failed transition."""
    try:
        await asyncio.to_thread(
            memory.update_mission, mission_id, {"status": previous_status or "PENDING"}
        )
    except Exception:
        logger.exception(
            "Rollback of mission %s to %s failed", mission_id, previous_status
        )
