"""Core RAG query and skills-file generator.

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
from brain.store import similarity_search

load_dotenv()

MODEL = "claude-sonnet-4-6"

QUERY_SYSTEM_PROMPT = """You are the Company Brain assistant. Answer questions using ONLY the information in the provided knowledge context. If the answer is not in the context, say so clearly — do not invent details, do not draw on general knowledge.

Cite sources inline using bracketed reference numbers like [1], [2], matching the numbered chunks in the context. Multiple citations are allowed: [1][3].

Be concise but complete. For policies, procedures, or named decisions, prefer direct quotes over paraphrase."""

SKILLS_SYSTEM_PROMPT = """You transform raw company knowledge into structured skill files. Given a process name and a numbered list of knowledge chunks (Slack threads, Notion docs, GitHub issues), produce a JSON skill file describing how the company handles that process.

Rules:
- Use ONLY information present in the chunks. Do not invent steps, owners, or rules. If the chunks don't say who owns a step, use "unspecified".
- "steps" must be ordered by execution. Each step has an integer "step", a concrete "action", an "owner" (role or "unspecified"), and "notes" (clarification, edge cases, or empty string).
- "decision_rules" capture if-this-then-that statements grounded in the chunks (e.g., "If outage breaches SLA, apply automatic credit per contract terms.").
- "exceptions" list scenarios in the chunks where the default process does not apply.
- "sources" list the source labels (formatted as "source_type:source_name") for the chunks that materially contributed. Drop chunks you didn't use.
- If the chunks are insufficient to populate a field, return an empty list for it. Do not fabricate."""

SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "process": {"type": "string"},
        "description": {"type": "string"},
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
        "exceptions": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "process",
        "description",
        "steps",
        "decision_rules",
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
    """Distill knowledge about a process into a structured skill file.

    One Voyage embed + one Supabase query + one Claude call. Skips the
    prose-answer step that query_brain does — Claude reads the raw chunks
    and structures them into JSON in a single pass.
    """
    query_embedding = embed_query(process_name)
    matches = similarity_search(query_embedding, k=top_k)

    if not matches:
        return {
            "process": process_name,
            "description": "No information found in the knowledge base for this process.",
            "steps": [],
            "decision_rules": [],
            "exceptions": [],
            "sources": [],
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
