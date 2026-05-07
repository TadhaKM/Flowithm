"""Manual verification script for drift detection.

NOT a pytest test — it hits live Supabase + Anthropic. Run before integrating
the feature into the main app:

    python -m brain.test_drift

It will:
  1. Save a baseline "Refunds" workflow saying "approved within 30 days"
  2. Save a NEW "Refunds" workflow saying "60 days for all customers"
  3. Run check_for_drift on the new one and print any conflicts
  4. Resolve the highest-severity conflict via 'accept'
  5. Print the updated (versioned) workflow
  6. Verify the original row is now archived
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from brain.drift import check_for_drift, resolve_conflict
from brain.store import get_client, save_workflow

load_dotenv()


OLD_WORKFLOW = {
    "process": "Customer refund handling",
    "description": "How we process refund requests from paying customers.",
    "trigger": "customer files a refund request via support email",
    "steps": [
        {"step": 1, "action": "Verify the request was filed within 30 days of purchase.", "owner": "Support agent", "notes": "If outside the window, escalate to a manager."},
        {"step": 2, "action": "Check the customer's account is in good standing.", "owner": "Support agent", "notes": ""},
        {"step": 3, "action": "Process the refund via Stripe.", "owner": "Support agent", "notes": ""},
    ],
    "decision_rules": [
        "Refunds are only approved within 30 days of purchase.",
        "Annual contracts cannot be cash-refunded mid-term.",
    ],
    "approvals": ["CFO sign-off required for refunds over $5,000."],
    "exceptions": ["Sandbox accounts are non-refundable."],
    "sources": ["notion:Refund Policy", "slack:cs-refunds"],
}

NEW_WORKFLOW = {
    "process": "Customer refund handling (updated)",
    "description": "Updated refund process per the new policy memo.",
    "trigger": "customer files a refund request via support email",
    "steps": [
        {"step": 1, "action": "Verify the request was filed within 60 days of purchase.", "owner": "Support agent", "notes": "Window expanded to 60 days for all customers."},
        {"step": 2, "action": "Process the refund via Stripe.", "owner": "Support agent", "notes": ""},
    ],
    "decision_rules": [
        "Refunds are approved within 60 days of purchase for all customers.",
    ],
    "approvals": [],
    "exceptions": [],
    "sources": ["notion:Refund Policy 2026-Q2"],
}

NEW_RAW_TEXT = (
    "From: Marcus Holt (CTO)\n"
    "Subject: Refund window expansion\n\n"
    "Effective immediately, customer refunds are approved within 60 days of "
    "purchase for all customers, regardless of plan type. The previous 30-day "
    "limit is replaced. Account-standing checks are no longer required as a "
    "separate step. CFO sign-off requirement is removed."
)


def main() -> None:
    print("\n=== 1. Saving baseline workflow ===")
    old_id = save_workflow(OLD_WORKFLOW, source="manual", source_metadata={}, raw_text="")
    print(f"  baseline id: {old_id}")

    print("\n=== 2. Saving new (contradicting) workflow ===")
    new_id = save_workflow(NEW_WORKFLOW, source="manual", source_metadata={}, raw_text=NEW_RAW_TEXT)
    print(f"  new id:      {new_id}")

    new_skill_for_drift = dict(NEW_WORKFLOW)
    new_skill_for_drift["id"] = new_id

    print("\n=== 3. Running check_for_drift ===")
    conflicts = check_for_drift(NEW_RAW_TEXT, new_skill_for_drift)
    if not conflicts:
        print("  ! no conflicts detected — Claude either ranked them as minor wording")
        print("    differences or the prompt missed. Inspect server logs.")
        return
    print(f"  {len(conflicts)} conflict(s):")
    for c in conflicts:
        print(f"    - [{c['severity']:6}] {c['conflict_type']:14} {c['conflict_description']}")
        print(f"        existing_rule:    {c['existing_rule']}")
        print(f"        new_evidence:     {c['new_evidence']}")
        print(f"        suggested_update: {c['suggested_update']}")

    print("\n=== 4. Resolving highest-severity conflict via 'accept' ===")
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    top = sorted(conflicts, key=lambda c: sev_rank.get(c["severity"], 3))[0]
    resolved = resolve_conflict(top["id"], action="accept", resolved_by="test_drift.py")
    print("  updated workflow:")
    print(json.dumps(resolved, indent=2, ensure_ascii=False, default=str))

    print("\n=== 5. Verifying old record is archived ===")
    client = get_client()
    refetched = (
        client.table("skills")
        .select("id,archived,archived_at,version,previous_version_id")
        .eq("id", top["existing_skill_id"])
        .single()
        .execute()
    )
    row = refetched.data or {}
    archived = bool(row.get("archived"))
    print(f"  existing_skill_id: {top['existing_skill_id']}")
    print(f"  archived:          {archived}")
    print(f"  archived_at:       {row.get('archived_at')}")
    if not archived:
        print("  ! FAIL — old row was not archived.")
        sys.exit(1)
    print("\nOK: drift round-trip works end-to-end.")


if __name__ == "__main__":
    main()
