"""brain.staleness — needs_review flag lifecycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from freezegun import freeze_time


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_flags_never_reviewed_old_skill(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "90")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        old_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)  # >90d before frozen now
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s1",
            "process_name": "Refunds",
            "created_at": _iso(old_dt),
            "reviewed_at": None,
            "needs_review": False,
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["newly_flagged"] == 1
    assert summary["flags_cleared"] == 0
    # The fake captured an update call with needs_review=True.
    update_calls = [
        c for c in mock_supabase.calls
        if c.get("action") == "update" and c.get("table") == "skills"
    ]
    assert any(c["payload"].get("needs_review") is True for c in update_calls)


def test_does_not_flag_new_skill(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "90")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        recent_dt = datetime(2026, 4, 28, tzinfo=timezone.utc)  # 10d ago
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s2",
            "process_name": "Onboarding",
            "created_at": _iso(recent_dt),
            "reviewed_at": None,
            "needs_review": False,
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["newly_flagged"] == 0
    assert summary["flags_cleared"] == 0


def test_flags_old_reviewed_skill(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "90")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        old_review = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s3",
            "process_name": "Deploys",
            "created_at": "2025-01-01T00:00:00+00:00",
            "reviewed_at": _iso(old_review),
            "needs_review": False,
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["newly_flagged"] == 1


def test_clears_flag_after_recent_review(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "90")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        recent_review = datetime(2026, 5, 1, tzinfo=timezone.utc)
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s4",
            "process_name": "Incident response",
            "created_at": "2025-01-01T00:00:00+00:00",
            "reviewed_at": _iso(recent_review),
            "needs_review": True,  # was flagged previously, now should clear
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["flags_cleared"] == 1
    assert summary["newly_flagged"] == 0


def test_threshold_env_var_short(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "1")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        two_days_ago = datetime(2026, 5, 6, tzinfo=timezone.utc)
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s5",
            "process_name": "Refunds",
            "created_at": _iso(two_days_ago),
            "reviewed_at": None,
            "needs_review": False,
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["newly_flagged"] == 1


def test_threshold_env_var_long(mock_supabase, monkeypatch):
    monkeypatch.setenv("STALE_THRESHOLD_DAYS", "90")
    from brain.staleness import run_staleness_check

    with freeze_time("2026-05-08T12:00:00+00:00"):
        two_days_ago = datetime(2026, 5, 6, tzinfo=timezone.utc)
        mock_supabase.rows_by_table["skills"] = [{
            "id": "s5b",
            "process_name": "Refunds",
            "created_at": _iso(two_days_ago),
            "reviewed_at": None,
            "needs_review": False,
            "archived": False,
            "org_id": "00000000-0000-0000-0000-000000000001",
        }]

        summary = run_staleness_check()

    assert summary["newly_flagged"] == 0


def test_mark_as_reviewed_clears_flag(mock_supabase):
    from brain.staleness import mark_as_reviewed

    mock_supabase.rows_by_table["skills"] = [{
        "id": "s6",
        "process_name": "Refunds",
        "needs_review": True,
        "needs_review_reason": "Last reviewed 120 days ago",
        "stale_flagged_at": "2026-05-01T00:00:00+00:00",
        "reviewed_at": None,
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    row = mark_as_reviewed("s6")

    assert row.get("needs_review") is False
    assert row.get("needs_review_reason") is None
    assert row.get("stale_flagged_at") is None
    assert row.get("reviewed_at") is not None
