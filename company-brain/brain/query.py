"""RAG query and workflow/skill-file generators.

Three public entry points and two distinct schemas:
  - query_brain(question, top_k)            — RAG Q&A over the chunks table
  - generate_skills_file(name, top_k)       — RAG-driven skill file (chunks → SKILL_SCHEMA)
  - generate_workflow_from_text(name, body) — text-driven workflow (paste → WORKFLOW_SCHEMA),
                                              persisted to the skills table for /history

SKILL_SCHEMA (returned by /skills) and WORKFLOW_SCHEMA (returned by /workflows/generate)
are intentionally different shapes — see the docstring on each near the bottom of
this file. The workflow path also persists, so its shape has to round-trip through
the skills table (see brain/store.py and brain/schema.sql).

CLI:
    python -m brain.query "How do we handle refunds?"
    python brain/query.py "How do we handle refunds?"
"""
# When run as a bare script, patch sys.path so the `brain.*` and `ingest.*`
# imports below resolve from the project root.
if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from typing import Any

import anthropic
from dotenv import load_dotenv

from brain.embedder import embed_query
from brain.store import save_workflow, similarity_search

load_dotenv()

MODEL = "claude-sonnet-4-6"

QUERY_SYSTEM_PROMPT = """You are the Company Brain assistant. Answer questions using ONLY the information in the provided knowledge context. If the answer is not in the context, say so clearly — do not invent details, do not draw on general knowledge.

Cite sources inline using bracketed reference numbers like [1], [2], matching the numbered chunks in the context. Multiple citations are allowed: [1][3].

Be concise but complete. For policies, procedures, or named decisions, prefer direct quotes over paraphrase."""

SKILLS_SYSTEM_PROMPT = """You transform raw company knowledge into structured skill files. Given a process name and a numbered list of knowledge chunks (Slack threads, Notion docs, GitHub issues), produce a JSON skill file describing how the company handles that process.

The output schema:
- "process": short name for the process.
- "trigger": short string describing the event that starts this process (e.g., "customer files refund request via support email", "p95 latency alert fires"). If unclear from the chunks, use "manual / on demand".
- "steps": ordered array. Each step has:
    - "step": integer position
    - "action": single imperative sentence describing what is done
    - "logic": an if/then rule that gates this specific step, or null if unconditional. Example: "If refund amount > $500, route to manager queue before processing." DO NOT confuse this with general decision_rules — logic is per-step.
    - "owner": role or named person responsible. If the chunks don't say, use "unspecified".
    - "notes": clarification, edge cases, or context for the step. Use null if there is nothing to add.
- "decision_rules": process-wide if/then rules (not tied to one step), as plain English strings.
- "approvals": authorization gates — anything requiring sign-off or escalation ("credits over 1 cycle require CEO approval"). Empty list if none.
- "exceptions": edge cases and overrides where the default process does not apply.
- "sources_summary": ONE sentence describing what kinds of sources informed this skill (e.g., "Drawn from #engineering-incidents Slack thread, the Notion on-call runbook, and GitHub issue #1232.").

Rules:
- Use ONLY information present in the chunks. Do not invent steps, owners, triggers, decision rules, or approvals.
- Distinguish per-step logic (in step.logic) from process-wide decision_rules. A rule that says "if amount > X, do Y" inside a specific step belongs in that step's logic. A rule like "annual contracts cannot be cash-refunded mid-term" belongs in decision_rules.
- If the chunks don't have enough information for a list field, return an empty list. For string fields, prefer a clear short summary over a fabricated long one."""

WORKFLOW_SYSTEM_PROMPT = """You transform raw source material (Slack threads, docs, runbooks, meeting notes) into structured workflow files. Given a process name and source material the user has pasted directly, produce a JSON workflow describing how the process actually runs.

Rules:
- Use ONLY information present in the source material. Do not invent steps, owners, triggers, decision rules, or approvals.
- "trigger" is a short string describing what kicks off this process (e.g., "customer files refund request via support email", "p95 latency alert fires in #engineering-incidents"). If the source doesn't make this clear, use "manual / on demand".
- "steps" must be ordered by execution. Each step has an integer "step", a concrete "action" (a single imperative sentence), an "owner" (role or person if specified, otherwise "unspecified"), and "notes" (any conditional or edge-case detail; empty string if none).
- "decision_rules" capture if-this-then-that statements grounded in the source.
- "approvals" list authorization gates explicitly mentioned ("CFO sign-off required for credits over 1 cycle"). Empty list if none.
- "exceptions" list scenarios in the source where the default process does not apply.
- "sources" list short labels for the inputs that contributed (e.g., "slack:engineering-incidents", "notion:Refund Policy"). If the source doesn't make labels obvious, use "user-pasted material".
- If the source doesn't contain enough info to populate a list field, return an empty list. Do not fabricate."""


# ---------------------------------------------------------------------------
# SKILL_SCHEMA — returned by /skills (generate_skills_file).
# ---------------------------------------------------------------------------
# Per-step logic, single sources_summary string. Distinct from WORKFLOW_SCHEMA
# below to keep the workflow UI's contract stable while the skill-file shape
# evolves.
SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "process": {"type": "string"},
        "trigger": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "action": {"type": "string"},
                    "logic": {"type": ["string", "null"]},
                    "owner": {"type": "string"},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["step", "action", "logic", "owner", "notes"],
                "additionalProperties": False,
            },
        },
        "decision_rules": {"type": "array", "items": {"type": "string"}},
        "approvals": {"type": "array", "items": {"type": "string"}},
        "exceptions": {"type": "array", "items": {"type": "string"}},
        "sources_summary": {"type": "string"},
    },
    "required": [
        "process",
        "trigger",
        "steps",
        "decision_rules",
        "approvals",
        "exceptions",
        "sources_summary",
    ],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# WORKFLOW_SCHEMA — returned by /workflows/generate (generate_workflow_from_text).
# ---------------------------------------------------------------------------
# Has `description` and `sources` (array). This shape is what /history rows
# and the workflow UI consume — don't change it without updating
# brain/store.py, brain/schema.sql, and ui/app/page.tsx together.
WORKFLOW_SCHEMA: dict[str, Any] = {
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
        "process",
        "description",
        "trigger",
        "steps",
        "decision_rules",
        "approvals",
        "exceptions",
        "sources",
    ],
    "additionalProperties": False,
}


def _confidence(scores: list[float]) -> str:
    """Map similarity scores from match_chunks (1 - cosine distance) to a label.

    Voyage embeddings: ~0.85+ is a near-paraphrase match, 0.6–0.8 is on-topic,
    <0.5 is usually unrelated. Top score is more telling than mean — one
    strong hit is enough for a good answer.
    """
    if not scores:
        return "low"
    top = max(scores)
    if top >= 0.75:
        return "high"
    if top >= 0.55:
        return "medium"
    return "low"


def _preview(text: str, n: int = 200) -> str:
    return text[:n] + ("…" if len(text) > n else "")


def query_brain(question: str, top_k: int = 6) -> dict[str, Any]:
    """Embed the question, retrieve top_k chunks, answer with Claude."""
    query_embedding = embed_query(question)
    matches = similarity_search(query_embedding, k=top_k)

    if not matches:
        return {
            "answer": "I couldn't find anything in the knowledge base that addresses that question.",
            "sources": [],
            "confidence": "low",
        }

    context = "\n\n".join(
        f"[{i}] {m['source_type']}:{m['source_name']}\n{m['content']}"
        for i, m in enumerate(matches, 1)
    )
    user_message = f"Knowledge context:\n\n{context}\n\nQuestion: {question}"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": QUERY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user_message}],
    )

    answer = "".join(b.text for b in message.content if b.type == "text")

    similarities = [float(m.get("similarity") or 0.0) for m in matches]
    sources = [
        {
            "source_type": m["source_type"],
            "source_name": m["source_name"],
            "content_preview": _preview(m["content"]),
        }
        for m in matches
    ]

    return {
        "answer": answer,
        "sources": sources,
        "confidence": _confidence(similarities),
    }


def generate_skills_file(process_name: str, top_k: int = 10) -> dict[str, Any]:
    """RAG-driven skill file: read chunks for the process, structure into SKILL_SCHEMA.

    Use generate_workflow_from_text() instead when the user pastes their own
    source material; that path skips retrieval entirely and produces the
    workflow shape (description + sources array).
    """
    query_embedding = embed_query(process_name)
    matches = similarity_search(query_embedding, k=top_k)

    if not matches:
        return {
            "process": process_name,
            "trigger": "",
            "steps": [],
            "decision_rules": [],
            "approvals": [],
            "exceptions": [],
            "sources_summary": "No information found in the knowledge base for this process.",
        }

    context = "\n\n".join(
        f"[{i}] {m['source_type']}:{m['source_name']}\n{m['content']}"
        for i, m in enumerate(matches, 1)
    )
    user_message = f"Process name: {process_name}\n\nKnowledge chunks:\n\n{context}"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SKILLS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SKILL_SCHEMA},
        },
        messages=[{"role": "user", "content": user_message}],
    )

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude refused to generate the skill file for safety reasons.")

    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)


def generate_workflow_from_text(
    name: str,
    content: str,
    source: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transform user-pasted source material into a structured workflow.

    No retrieval — the user supplies all source material directly. After
    generation, the workflow is persisted to the skills table so /history
    can replay it. Persistence failures don't block the response.

    Optional `source` and `source_metadata` are passed through to the store
    layer for provenance (e.g. "slack" + channel/thread/user). When the row
    is saved successfully, its UUID is added to the returned dict as `id`
    so callers (UI, Slack bot) can build deeplinks.
    """
    user_message = f"Process name: {name}\n\nSource material:\n\n{content}"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": WORKFLOW_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": WORKFLOW_SCHEMA},
        },
        messages=[{"role": "user", "content": user_message}],
    )

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude refused to generate the workflow for safety reasons.")

    text = next((b.text for b in message.content if b.type == "text"), "")
    workflow = json.loads(text)

    workflow_id = ""
    try:
        # Persist the raw input alongside the structured output so the
        # /brain/[id] detail page can offer a "Re-extract" action.
        workflow_id = save_workflow(
            workflow,
            source=source,
            source_metadata=source_metadata,
            raw_text=content,
        )
    except Exception as exc:
        # Don't block the response on persistence failures — the user still
        # gets their workflow, /history just won't include this run.
        print(f"[workflow] save_workflow failed: {exc}", flush=True)

    if workflow_id:
        workflow["id"] = workflow_id
    return workflow


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Query the Company Brain.")
    parser.add_argument("question", help="The question to ask the brain")
    parser.add_argument("--top-k", type=int, default=6, help="Number of chunks to retrieve")
    parser.add_argument(
        "--skill",
        action="store_true",
        help="Generate a structured skill file for the given process instead of a plain answer",
    )
    args = parser.parse_args()

    result = (
        generate_skills_file(args.question)
        if args.skill
        else query_brain(args.question, top_k=args.top_k)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
