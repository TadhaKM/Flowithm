"""Skills staleness detection.

Skills that haven't been reviewed within $STALE_THRESHOLD_DAYS get flagged
with `needs_review=true` + a human-readable reason. Agents (via /api/v1/skills)
and humans (via the /brain dashboard) can see the flag and either escalate or
revisit before acting on a stale workflow.

Public surface:
    run_staleness_check()    — scheduler hook; returns counts summary
    mark_as_reviewed(skill_id) — clears the flag + bumps reviewed_at
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from brain.logger import get_logger
from brain.store import get_client

logger = get_logger("flowithm.staleness")


def _stale_days() -> int:
    """Read at call time so tests / runtime overrides take effect without restart."""
    try:
        return int(os.getenv("STALE_THRESHOLD_DAYS", "90"))
    except ValueError:
        return 90


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def run_staleness_check(org_id: str | None = None) -> dict[str, Any]:
    """Flag/clear skills past the staleness threshold via a single SQL RPC.
    No longer loads every skill into Python — the comparison runs in Postgres."""
    from brain.store import _default_org_id

    org = org_id or _default_org_id()
    client = get_client()
    threshold_days = _stale_days()

    try:
        resp = client.rpc("run_staleness_pass", {
            "p_org_id": org,
            "p_threshold_days": threshold_days,
        }).execute()
        row = resp.data
        if isinstance(row, list) and row:
            row = row[0]
        if isinstance(row, dict) and "flagged_count" in row:
            flagged = int(row["flagged_count"])
            cleared = int(row.get("cleared_count", 0))
        else:
            # RPC returned unexpected shape — fall back to Python path.
            flagged, cleared = _staleness_check_legacy(client, org, threshold_days)
    except Exception:
        # Fallback: RPC not yet migrated — run the legacy Python path.
        flagged, cleared = _staleness_check_legacy(client, org, threshold_days)

    summary = {
        "skills_checked": 0,  # not computed in SQL path (avoids a count)
        "newly_flagged": flagged,
        "flags_cleared": cleared,
        "threshold_days": threshold_days,
    }
    logger.info("staleness check complete", extra={
        "flagged": flagged,
        "cleared": cleared,
        "threshold_days": threshold_days,
    })
    return summary


def _staleness_check_legacy(client, org: str, threshold_days: int) -> tuple[int, int]:
    """Fallback for deployments that haven't run the latest schema.sql yet."""
    threshold = _now_utc() - timedelta(days=threshold_days)
    skills = (
        client.table("skills")
        .select("id,process_name,generated_at,reviewed_at,needs_review")
        .eq("archived", False)
        .eq("org_id", org)
        .execute()
        .data
        or []
    )
    to_flag: list[str] = []
    to_clear: list[str] = []
    for skill in skills:
        created_at = _parse_iso(skill.get("generated_at"))
        if created_at is None:
            continue
        reviewed_at = _parse_iso(skill.get("reviewed_at"))
        currently_flagged = bool(skill.get("needs_review"))
        should_flag = (
            (reviewed_at is None and created_at < threshold)
            or (reviewed_at is not None and reviewed_at < threshold)
        )
        if should_flag and not currently_flagged:
            to_flag.append(str(skill["id"]))
        elif (not should_flag) and currently_flagged:
            to_clear.append(str(skill["id"]))
    if to_flag:
        client.table("skills").update({
            "needs_review": True,
            "needs_review_reason": "Hasn't been reviewed recently",
            "stale_flagged_at": _now_utc().isoformat(),
        }).in_("id", to_flag).execute()
    if to_clear:
        client.table("skills").update({
            "needs_review": False,
            "needs_review_reason": None,
            "stale_flagged_at": None,
        }).in_("id", to_clear).execute()
    return len(to_flag), len(to_clear)


def mark_as_reviewed(skill_id: str, org_id: str | None = None) -> dict[str, Any]:
    """Set reviewed_at=now() and clear every staleness flag on the row."""
    from brain.store import _default_org_id

    client = get_client()
    now_iso = _now_utc().isoformat()
    result = (
        client.table("skills")
        .update({
            "reviewed_at": now_iso,
            "needs_review": False,
            "needs_review_reason": None,
            "stale_flagged_at": None,
        })
        .eq("id", skill_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else {}
