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
    """Walk every active skill in the current org; flag those past the
    staleness threshold, clear the flag on any reviewed since."""
    from brain.store import _default_org_id

    org = org_id or _default_org_id()
    client = get_client()
    threshold_days = _stale_days()
    threshold = _now_utc() - timedelta(days=threshold_days)
    flagged = 0
    cleared = 0

    skills = (
        client.table("skills")
        .select("id,process_name,created_at,reviewed_at,needs_review")
        .eq("archived", False)
        .eq("org_id", org)
        .execute()
        .data
        or []
    )

    for skill in skills:
        created_at = _parse_iso(skill.get("created_at")) or _parse_iso(skill.get("generated_at"))
        # Some early rows may have only generated_at, not created_at.
        if created_at is None:
            continue
        reviewed_at = _parse_iso(skill.get("reviewed_at"))
        currently_flagged = bool(skill.get("needs_review"))

        should_flag = False
        reason: str | None = None
        if reviewed_at is None and created_at < threshold:
            should_flag = True
            days_old = (_now_utc() - created_at).days
            reason = f"Never reviewed — created {days_old} days ago"
        elif reviewed_at is not None and reviewed_at < threshold:
            should_flag = True
            days_since = (_now_utc() - reviewed_at).days
            reason = f"Last reviewed {days_since} days ago"

        if should_flag and not currently_flagged:
            client.table("skills").update({
                "needs_review": True,
                "needs_review_reason": reason,
                "stale_flagged_at": _now_utc().isoformat(),
            }).eq("id", skill["id"]).execute()
            flagged += 1
            logger.info("flagged stale skill", extra={
                "process": skill.get("process_name"), "reason": reason,
            })
        elif (not should_flag) and currently_flagged:
            client.table("skills").update({
                "needs_review": False,
                "needs_review_reason": None,
                "stale_flagged_at": None,
            }).eq("id", skill["id"]).execute()
            cleared += 1

    summary = {
        "skills_checked": len(skills),
        "newly_flagged": flagged,
        "flags_cleared": cleared,
        "threshold_days": threshold_days,
    }
    logger.info("staleness check complete", extra={
        "flagged": flagged,
        "cleared": cleared,
        "checked": len(skills),
        "threshold_days": threshold_days,
    })
    return summary


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
