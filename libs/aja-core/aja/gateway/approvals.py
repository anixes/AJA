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
from typing import Tuple

logger = logging.getLogger(__name__)

_TERMINAL_OR_ACTIVE_STATUSES = {"ACTIVE", "DONE", "FAILED", "REJECTED"}


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
    """
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
        try:
            parsed_expires_at = datetime.fromisoformat(
                str(expires_at).replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) > parsed_expires_at:
                return False, f"\u23f3 Mission {mission_id} approval has expired."
        except ValueError as e:
            logger.warning(
                "Could not parse approval expiry %r for %s: %s",
                expires_at,
                mission_id,
                e,
            )

    if status in _TERMINAL_OR_ACTIVE_STATUSES:
        return (
            False,
            f"\u2139\ufe0f Mission {mission_id} already handled (status: {status}).",
        )

    if action == "approve":
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
        logger.info(
            "[%s] user %s approved mission %s", platform, user_id, mission_id
        )
        return True, f"\u2705 Mission {mission_id} Approved."

    # Reject
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
    logger.info("[%s] user %s rejected mission %s", platform, user_id, mission_id)
    return True, f"\u274c Mission {mission_id} Rejected."
