"""Drift detection — does new content contradict any existing skill?

Two entry points:
  check_for_drift(content, new_skill)
      Triggered after every workflow generation (UI or Slack). new_skill is
      a fully-structured workflow JSON; the model contrasts it against
      existing skills as peers.

  check_chunks_against_skills(chunks)
      Triggered by the scheduler after every ingest cycle. Each chunk is
      raw incoming knowledge (Slack thread, Notion page section, etc.).
      Per chunk, find the most-similar skill and ask Claude whether the
      chunk contradicts the skill's rules.

Public surface:
    check_for_drift(new_content, new_skill)
    check_chunks_against_skills(chunks)
    schedule_drift_check(new_content, new_skill)    — fire-and-forget
    resolve_conflict(id, action, resolved_by)       — accept | dismiss | snooze
    get_unresolved_conflicts(include_snoozed=False) — feed for /conflicts
    get_conflict_history(skill_id)                  — full history per skill
"""
from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from dotenv import load_dotenv

from brain.anthropic_client import messages_create
from brain.embedder import get_embedding
from brain.logger import get_logger
from brain.store import get_client, save_workflow

load_dotenv()

log = get_logger("flowithm.drift")

MODEL = "claude-sonnet-4-6"
SKILLS_TABLE = "skills"
CONFLICTS_TABLE = "conflicts"
CANDIDATES_LIMIT = 20
SNOOZE_DAYS = 7
_DRIFT_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="drift-claude")
_anthropic: anthropic.Anthropic | None = None


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic()
    return _anthropic

DRIFT_SYSTEM_PROMPT = (
    "You are a knowledge consistency checker. You compare new company "
    "knowledge against existing documented processes and identify "
    "contradictions, updates, or conflicts. Be specific and precise. "
    "Only flag genuine conflicts — not minor wording differences."
)

APPLY_UPDATE_SYSTEM_PROMPT = (
    "You apply a single update instruction to an existing structured "
    "workflow JSON. Preserve the original structure and only modify the "
    "fields the update instruction targets. Return the full updated "
    "workflow as JSON in the same shape as the input."
)

DRIFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_skill_id": {"type": "string"},
                    "existing_process_name": {"type": "string"},
                    "conflict_type": {"type": "string", "enum": ["contradiction", "update", "expansion", "deprecation"]},
                    "conflict_description": {"type": "string"},
                    "existing_rule": {"type": "string"},
                    "new_evidence": {"type": "string"},
                    "suggested_update": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": [
                    "existing_skill_id", "existing_process_name", "conflict_type",
                    "conflict_description", "existing_rule", "new_evidence",
                    "suggested_update", "severity",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["conflicts"],
    "additionalProperties": False,
}

# Mirrors WORKFLOW_SCHEMA in brain/query.py so save_workflow round-trips.
_WORKFLOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "process": {"type": "string"},
        "description": {"type": "string"},
        "trigger": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "action": {"type": "string"},
                    "owner": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["step", "action", "owner", "notes"],
                "additionalProperties": False,
            },
        },
        "decision_rules": {"type": "array", "items": {"type": "string"}},
        "approvals": {"type": "array", "items": {"type": "string"}},
        "exceptions": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "process", "description", "trigger", "steps",
        "decision_rules", "approvals", "exceptions", "sources",
    ],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_to_epoch(iso: str) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _skill_summary(skill: dict[str, Any]) -> str:
    """Concatenate the most semantically meaningful fields of a skill row."""
    parts = [
        skill.get("process_name") or "",
        skill.get("description") or "",
        skill.get("process_trigger") or "",
    ]
    for s in (skill.get("steps") or [])[:5]:
        parts.append(str(s.get("action") or ""))
    for r in (skill.get("decision_rules") or [])[:3]:
        parts.append(str(r))
    return "\n".join(p for p in parts if p)


def _rank_candidates_via_rpc(
    new_content: str, org_id: str, limit: int
) -> list[dict[str, Any]]:
    """Top-N skills by pgvector cosine similarity via the match_skills RPC.

    P-2: replaces the old approach that re-embedded every skill on every
    workflow generation. One Voyage call (embed new_content) + one Postgres
    ANN query (match_skills) regardless of skill count.
    """
    query_vec = get_embedding(new_content)
    client = get_client()
    resp = client.rpc("match_skills", {
        "query_embedding": query_vec,
        "match_count": limit,
        "target_org_id": org_id,
    }).execute()
    return resp.data or []


def _strip_for_llm(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(skill.get("id") or ""),
        "process": skill.get("process_name") or "",
        "description": skill.get("description") or "",
        "trigger": skill.get("process_trigger") or "",
        "steps": skill.get("steps") or [],
        "decision_rules": skill.get("decision_rules") or [],
        "approvals": skill.get("approvals") or [],
        "exceptions": skill.get("exceptions") or [],
    }


# ---------------------------------------------------------------------------
# Public: check
# ---------------------------------------------------------------------------

def check_for_drift(
    new_content: str,
    new_skill: dict[str, Any],
    org_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run the consistency check; persist any conflicts; return the inserted rows.

    Always non-raising — failures are logged and yield an empty list so
    callers (background threads, generation hooks) don't crash on transient
    Supabase or Anthropic issues.
    """
    try:
        from brain.store import _default_org_id

        org = org_id or _default_org_id()
        client = get_client()
        new_skill_id = new_skill.get("id")

        # P-2: use match_skills RPC (pgvector ANN) instead of loading
        # every skill + re-embedding them all via Voyage.
        candidates = _rank_candidates_via_rpc(new_content, org, CANDIDATES_LIMIT)
        candidates = [c for c in candidates if str(c.get("id")) != str(new_skill_id or "")]
        if not candidates:
            return []

        existing_subset = [_strip_for_llm(s) for s in candidates]

        anthropic_client = _get_anthropic()
        user_message = (
            "New process just extracted:\n"
            f"{json.dumps(new_skill, indent=2, ensure_ascii=False, default=str)}\n\n"
            "Existing skills files to check against:\n"
            f"{json.dumps(existing_subset, indent=2, ensure_ascii=False, default=str)}\n\n"
            "Return a JSON object with a `conflicts` array. Each conflict requires "
            "existing_skill_id (use the id from the existing skills above), "
            "existing_process_name, conflict_type "
            "(contradiction|update|expansion|deprecation), conflict_description "
            "(one clear sentence), existing_rule (the specific rule that conflicts), "
            "new_evidence (the specific part of new content that conflicts), "
            "suggested_update (exactly how the existing skill should change), "
            "severity (high|medium|low). Return an empty array if no genuine "
            "conflicts exist."
        )

        message = messages_create(
            anthropic_client,
            model=MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": DRIFT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": DRIFT_SCHEMA},
            },
            messages=[{"role": "user", "content": user_message}],
        )
        if message.stop_reason == "refusal":
            log.warning("Claude refused drift check")
            return []

        text = next((b.text for b in message.content if b.type == "text"), "")
        parsed = json.loads(text or "{}")
        raw_conflicts = parsed.get("conflicts") or []
        if not raw_conflicts:
            return []

        candidate_ids = {str(c.get("id") or "") for c in candidates}
        rows = []
        for c in raw_conflicts:
            existing_id = str(c.get("existing_skill_id") or "")
            if existing_id not in candidate_ids:
                # Defend against hallucinated skill ids.
                continue
            rows.append({
                "existing_skill_id": existing_id,
                "new_skill_id": str(new_skill_id) if new_skill_id else None,
                "existing_process_name": c.get("existing_process_name") or "",
                "conflict_type": c.get("conflict_type"),
                "conflict_description": c.get("conflict_description") or "",
                "existing_rule": c.get("existing_rule") or "",
                "new_evidence": c.get("new_evidence") or "",
                "suggested_update": c.get("suggested_update") or "",
                "severity": c.get("severity") or "medium",
                "org_id": org,
            })

        if not rows:
            return []
        insert = client.table(CONFLICTS_TABLE).insert(rows).execute()
        inserted = insert.data or []
        # Note: `process` collides with a reserved LogRecord attribute
        # (the OS pid). Use `process_name` for the structured field.
        log.info("conflicts recorded", extra={
            "org_id": org,
            "process_name": new_skill.get("process"),
            "count": len(inserted),
        })
        return inserted

    except Exception as exc:
        log.error("check_for_drift failed", exc_info=True,
                  extra={"error": str(exc)})
        return []


CHUNK_DRIFT_SYSTEM_PROMPT = (
    "You are a knowledge consistency checker. You compare ONE new piece of "
    "incoming company content against ONE existing documented process and "
    "decide whether the new content contradicts a specific rule in the "
    "process. Be strict — only flag genuine factual conflicts (e.g. the "
    "process says X is required but the new content says X is no longer "
    "required). Ignore overlap that merely restates or expands without "
    "contradicting."
)

CHUNK_DRIFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_conflict": {"type": "boolean"},
        "conflict_type": {"type": ["string", "null"], "enum": ["contradiction", "update", "expansion", "deprecation", None]},
        "conflict_description": {"type": ["string", "null"]},
        "existing_rule": {"type": ["string", "null"]},
        "new_evidence": {"type": ["string", "null"]},
        "suggested_update": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
    },
    "required": [
        "is_conflict", "conflict_type", "conflict_description",
        "existing_rule", "new_evidence", "suggested_update", "severity",
    ],
    "additionalProperties": False,
}

# Tunables for check_chunks_against_skills. Kept here so a future config
# pass can lift them without hunting through the function body.
CHUNK_MIN_SKILL_SIMILARITY = 0.45  # below this, the chunk is likely off-topic
CHUNK_TOP_K_CANDIDATES = 2          # ask Claude about the top-N most similar skills
CHUNK_DRIFT_PREVIEW_CHARS = 1500    # cap chunk length sent to the LLM


def check_chunks_against_skills(
    chunks: list,
    org_id: str | None = None,
    precomputed_embeddings: list[list[float]] | None = None,
) -> list[dict[str, Any]]:
    """Per-chunk drift check against existing non-archived skills.

    For each incoming chunk:
      1. Embed it (one batch call for the whole list).
      2. Cosine against every skill's pre-computed summary_embedding.
      3. For the top-N most-similar skills above CHUNK_MIN_SKILL_SIMILARITY,
         ask Claude (one call per pair) whether the chunk contradicts the
         skill's rules.
      4. Persist any conflicts as 'unresolved' rows in the conflicts table.

    Returns the inserted conflict rows. Always non-raising — the scheduler
    surfaces failures via ingest_runs.errors instead of crashing the cycle.
    """
    if not chunks:
        return []

    try:
        from brain.store import _default_org_id

        org = org_id or _default_org_id()
        client = get_client()

        chunk_texts = [_chunk_content(c)[:CHUNK_DRIFT_PREVIEW_CHARS] for c in chunks]
        usable = [(i, t) for i, t in enumerate(chunk_texts) if t.strip()]
        if not usable:
            return []

        # P-3: use match_skills RPC per chunk (pgvector ANN) instead of
        # loading ALL skills into Python and doing cosine in a loop.
        # Then run the per-(chunk, skill) Claude calls concurrently.
        anthropic_client = _get_anthropic()

        # Build list of (chunk_index, chunk_text, skill_dict) work items.
        # M-4: reuse precomputed embeddings when available instead of
        # re-calling Voyage (doubles Voyage spend otherwise).
        work_items: list[tuple[int, str, dict[str, Any]]] = []
        for usable_idx, (i_chunk, text) in enumerate(usable):
            if precomputed_embeddings and usable_idx < len(precomputed_embeddings):
                vec = precomputed_embeddings[usable_idx]
            else:
                vec = get_embedding(text)
            resp = client.rpc("match_skills", {
                "query_embedding": vec,
                "match_count": CHUNK_TOP_K_CANDIDATES,
                "target_org_id": org,
            }).execute()
            for skill in (resp.data or []):
                sim = skill.get("similarity", 0)
                if sim >= CHUNK_MIN_SKILL_SIMILARITY:
                    work_items.append((i_chunk, text, skill))

        if not work_items:
            return []

        # P-3: run Claude calls concurrently via thread pool.
        def _check_one(item: tuple[int, str, dict[str, Any]]) -> dict[str, Any] | None:
            i_chunk, chunk_text, skill = item
            hit = _check_chunk_against_skill(anthropic_client, chunk_text, skill)
            if not hit:
                return None
            return {
                "existing_skill_id": str(skill["id"]),
                "new_skill_id": None,
                "existing_process_name": skill.get("process_name") or "",
                "conflict_type": hit.get("conflict_type") or "contradiction",
                "conflict_description": hit.get("conflict_description") or "",
                "existing_rule": hit.get("existing_rule") or "",
                "new_evidence": hit.get("new_evidence") or _chunk_content(chunks[i_chunk])[:240],
                "suggested_update": hit.get("suggested_update") or "",
                "severity": hit.get("severity") or "medium",
                "org_id": org,
            }

        inserted: list[dict[str, Any]] = []
        futures = [_DRIFT_POOL.submit(_check_one, item) for item in work_items]
        for future in as_completed(futures):
            row = future.result()
            if row is None:
                continue
            try:
                ins = client.table(CONFLICTS_TABLE).insert(row).execute()
                inserted.extend(ins.data or [])
            except Exception as exc:
                log.error("conflict insert failed", exc_info=True,
                          extra={"error": str(exc), "org_id": org})

        if inserted:
            log.info("chunk-vs-skill conflicts recorded",
                     extra={"org_id": org, "count": len(inserted)})
        return inserted

    except Exception as exc:
        log.error("check_chunks_against_skills failed", exc_info=True,
                  extra={"error": str(exc)})
        return []


def _chunk_content(chunk: Any) -> str:
    """Accept a Chunk dataclass OR a {'content': ...} dict for flexibility."""
    if hasattr(chunk, "content"):
        return getattr(chunk, "content") or ""
    if isinstance(chunk, dict):
        return chunk.get("content") or ""
    return str(chunk)


def _check_chunk_against_skill(
    anthropic_client, chunk_text: str, skill: dict[str, Any]
) -> dict[str, Any] | None:
    """One Claude call: does THIS chunk contradict THIS skill? Returns a
    conflict dict on hit, None otherwise. Non-raising; logs and returns
    None on transient failures."""
    try:
        skill_for_llm = {
            "process": skill.get("process_name") or "",
            "description": skill.get("description") or "",
            "trigger": skill.get("process_trigger") or "",
            "steps": skill.get("steps") or [],
            "decision_rules": skill.get("decision_rules") or [],
            "approvals": skill.get("approvals") or [],
            "exceptions": skill.get("exceptions") or [],
        }
        user_message = (
            "Existing process:\n"
            f"{json.dumps(skill_for_llm, indent=2, ensure_ascii=False, default=str)}\n\n"
            "New incoming knowledge (a single chunk from Slack/Notion/etc.):\n"
            f"{chunk_text[:CHUNK_DRIFT_PREVIEW_CHARS]}\n\n"
            "Decide: does this new chunk contradict, update, expand, or "
            "deprecate a specific rule in the existing process? Set "
            "is_conflict=false if not. If yes, set is_conflict=true and "
            "fill conflict_type, severity, and the rule + evidence + "
            "suggested_update strings."
        )
        msg = messages_create(
            anthropic_client,
            model=MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": CHUNK_DRIFT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": CHUNK_DRIFT_SCHEMA},
            },
            messages=[{"role": "user", "content": user_message}],
        )
        if msg.stop_reason == "refusal":
            return None
        text = next((b.text for b in msg.content if b.type == "text"), "")
        parsed = json.loads(text or "{}")
        return parsed if parsed.get("is_conflict") else None
    except Exception as exc:
        log.error("_check_chunk_against_skill failed", exc_info=True,
                  extra={"error": str(exc), "skill": skill.get("process_name")})
        return None


def schedule_drift_check(
    new_content: str, new_skill: dict[str, Any], org_id: str | None = None
) -> None:
    """Fire-and-forget: run check_for_drift in a daemon thread."""
    def _worker():
        try:
            check_for_drift(new_content, new_skill, org_id=org_id)
        except Exception as exc:
            log.error("background drift check failed", exc_info=True,
                      extra={"error": str(exc)})
    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Public: list / history
# ---------------------------------------------------------------------------

def get_unresolved_conflicts(
    include_snoozed: bool = False, org_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Conflicts still needing a decision (scoped to current org)."""
    from brain.store import _default_org_id

    org = org_id or _default_org_id()
    client = get_client()
    if include_snoozed:
        rows = (
            client.table(CONFLICTS_TABLE)
            .select("*")
            .eq("org_id", org)
            .in_("status", ["unresolved", "snoozed"])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
    else:
        unresolved = (
            client.table(CONFLICTS_TABLE)
            .select("*")
            .eq("org_id", org)
            .eq("status", "unresolved")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        snoozed_expired = (
            client.table(CONFLICTS_TABLE)
            .select("*")
            .eq("org_id", org)
            .eq("status", "snoozed")
            .lt("snoozed_until", _now_iso())
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        rows = unresolved + snoozed_expired

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (
        sev_rank.get(r.get("severity"), 3),
        -_iso_to_epoch(r.get("created_at") or ""),
    ))

    _hydrate_skill_meta(client, rows, org=org)
    return rows


def get_conflict_history(skill_id: str, org_id: str | None = None) -> list[dict[str, Any]]:
    """Every conflict (any status) for a given skill_id, newest first."""
    from brain.store import _default_org_id

    client = get_client()
    result = (
        client.table(CONFLICTS_TABLE)
        .select("*")
        .eq("existing_skill_id", skill_id)
        .eq("org_id", org_id or _default_org_id())
        .order("created_at", desc=True)
        .execute()
    )
    org = org_id or _default_org_id()
    rows = result.data or []
    _hydrate_skill_meta(client, rows, org=org)
    return rows


def _hydrate_skill_meta(client, rows: list[dict[str, Any]], org: str | None = None) -> None:
    """Annotate each row with the live process_name/trigger and a
    `targets_archived_version` flag based on the referenced skill's
    archived state. UI uses this to gate the Accept button."""
    skill_ids = list({r["existing_skill_id"] for r in rows if r.get("existing_skill_id")})
    skill_meta: dict[str, dict[str, Any]] = {}
    if skill_ids:
        q = (
            client.table(SKILLS_TABLE)
            .select("id,process_name,process_trigger,archived")
            .in_("id", skill_ids)
        )
        if org:
            q = q.eq("org_id", org)
        sk = q.execute()
        for s in sk.data or []:
            skill_meta[str(s["id"])] = {
                "process_name": s.get("process_name") or "",
                "trigger": s.get("process_trigger") or "",
                "archived": bool(s.get("archived")),
            }
    for r in rows:
        sid = str(r.get("existing_skill_id") or "")
        meta = skill_meta.get(sid, {})
        r["existing_process_trigger"] = meta.get("trigger", "")
        if meta.get("process_name"):
            r["existing_process_name"] = meta["process_name"]
        # If the skill row is gone (deleted) we can't accept either — treat
        # as targets_archived_version=true so the UI keeps Accept disabled.
        r["targets_archived_version"] = meta.get("archived", True) if meta else True


# ---------------------------------------------------------------------------
# Public: resolve
# ---------------------------------------------------------------------------

def resolve_conflict(
    conflict_id: str, action: str, resolved_by: str, org_id: str | None = None
) -> dict[str, Any]:
    """Apply 'accept' | 'dismiss' | 'snooze' to a conflict.

    accept   -> Claude rewrites the existing skill per suggested_update,
                saves a NEW skills row with version + 1 + previous_version_id,
                archives the old row, marks the conflict accepted; returns
                the new workflow JSON (with id, version).
    dismiss  -> marks the conflict dismissed; returns the conflict row.
    snooze   -> status='snoozed', snoozed_until = now + SNOOZE_DAYS;
                returns the conflict row.
    """
    if action not in {"accept", "dismiss", "snooze"}:
        raise ValueError(f"unknown action: {action!r}")

    from brain.store import _default_org_id

    org = org_id or _default_org_id()
    client = get_client()
    fetched = (
        client.table(CONFLICTS_TABLE)
        .select("*")
        .eq("id", conflict_id)
        .eq("org_id", org)
        .limit(1)
        .execute()
    )
    rows = fetched.data or []
    if not rows:
        raise LookupError(f"conflict not found: {conflict_id}")
    conflict = rows[0]

    if action == "dismiss":
        return _update_conflict_status(client, conflict_id, {
            "status": "dismissed",
            "resolved_by": resolved_by,
            "resolved_at": _now_iso(),
        })

    if action == "snooze":
        until = (datetime.now(timezone.utc) + timedelta(days=SNOOZE_DAYS)).isoformat()
        return _update_conflict_status(client, conflict_id, {
            "status": "snoozed",
            "snoozed_until": until,
            "resolved_by": resolved_by,
            "resolved_at": _now_iso(),
        })

    # accept
    skill_resp = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("id", conflict["existing_skill_id"])
        .eq("org_id", org)
        .limit(1)
        .execute()
    )
    skill_rows = skill_resp.data or []
    if not skill_rows:
        raise LookupError(f"existing skill not found: {conflict['existing_skill_id']}")
    old_skill = skill_rows[0]
    if old_skill.get("archived"):
        # The UI disables Accept in this case; this is the API-level guard
        # so a stale request can't fork from an archived version.
        raise ValueError(
            "cannot accept a conflict that targets an archived version of the skill — "
            "dismiss it and re-run drift detection if still relevant"
        )

    updated_workflow = _apply_update_via_claude(old_skill, conflict["suggested_update"])
    new_version = int(old_skill.get("version") or 1) + 1

    # Compute summary embedding before the transactional INSERT so the
    # new skill is immediately visible to /api/v1/skills/match (H-7).
    summary_embedding = None
    try:
        summary_text = (
            (updated_workflow.get("process") or "") + "\n" +
            (updated_workflow.get("description") or "") + "\n" +
            (updated_workflow.get("trigger") or "")
        ).strip()
        if summary_text:
            summary_embedding = get_embedding(summary_text)
    except Exception as exc:
        log.error("accept summary embedding failed", exc_info=True,
                  extra={"error": str(exc)})

    # B-1 revised: archive old → INSERT new → cascade → mark accepted
    # all inside one Postgres transaction. Avoids the unique-active-name
    # trap and orphan version=1 skills.
    resp = client.rpc("accept_conflict", {
        "p_old_skill_id": str(old_skill["id"]),
        "p_conflict_id": conflict_id,
        "p_new_version": new_version,
        "p_resolved_by": resolved_by,
        "p_org_id": org,
        "p_process_name": updated_workflow.get("process") or "",
        "p_description": updated_workflow.get("description") or "",
        "p_process_trigger": updated_workflow.get("trigger") or "",
        "p_steps": updated_workflow.get("steps") or [],
        "p_decision_rules": updated_workflow.get("decision_rules") or [],
        "p_approvals": updated_workflow.get("approvals") or [],
        "p_exceptions": updated_workflow.get("exceptions") or [],
        "p_sources": updated_workflow.get("sources") or [],
        "p_source": old_skill.get("source") or "manual",
        "p_source_metadata": old_skill.get("source_metadata") or {},
        "p_raw_text": old_skill.get("raw_text") or "",
        "p_summary_embedding": summary_embedding,
    }).execute()

    new_id = resp.data if isinstance(resp.data, str) else str(resp.data or "")
    updated_workflow["id"] = new_id
    updated_workflow["version"] = new_version
    updated_workflow["previous_version_id"] = str(old_skill["id"])
    return updated_workflow


def _update_conflict_status(client, conflict_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    result = client.table(CONFLICTS_TABLE).update(patch).eq("id", conflict_id).execute()
    rows = result.data or []
    if not rows:
        raise LookupError(f"conflict update returned no row: {conflict_id}")
    return rows[0]


def _apply_update_via_claude(old_skill: dict[str, Any], suggested_update: str) -> dict[str, Any]:
    """Mechanically apply the suggested_update to an existing workflow JSON."""
    workflow = {
        "process": old_skill.get("process_name") or "",
        "description": old_skill.get("description") or "",
        "trigger": old_skill.get("process_trigger") or "",
        "steps": old_skill.get("steps") or [],
        "decision_rules": old_skill.get("decision_rules") or [],
        "approvals": old_skill.get("approvals") or [],
        "exceptions": old_skill.get("exceptions") or [],
        "sources": old_skill.get("sources") or [],
    }
    user_message = (
        f"Existing workflow:\n{json.dumps(workflow, indent=2, ensure_ascii=False)}\n\n"
        f"Update instruction:\n{suggested_update}\n\n"
        "Return the full updated workflow JSON, preserving fields not mentioned in the instruction."
    )
    message = messages_create(
        _get_anthropic(),
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": APPLY_UPDATE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": _WORKFLOW_SCHEMA},
        },
        messages=[{"role": "user", "content": user_message}],
    )
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude refused to apply the update")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text or "{}")
