"""FlowBrain agent demo — proves a real Claude agent follows company-specific
workflows instead of guessing.

Simulates a customer-support agent at Loopline (demo company) handling three
realistic refund scenarios. For each one, Claude calls FlowBrain's
GET /api/v1/skills/match to retrieve the exact workflow before responding —
including the edge cases generic AI would get wrong.

Run from the company-brain directory:
    python demo/agent_demo.py

Reads from .env:
    ANTHROPIC_API_KEY   — Anthropic API key
    FLOWBRAIN_API_URL   — base URL of the deployed FlowBrain API (Railway)
    FLOWBRAIN_API_KEY   — a minted FlowBrain API key (fb_live_...)
"""
import json
import os
import sys
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

# Resolve .env relative to the project root so the script runs from any cwd.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = "claude-opus-4-7"

_missing = [
    name
    for name in ("ANTHROPIC_API_KEY", "FLOWBRAIN_API_URL", "FLOWBRAIN_API_KEY")
    if not os.getenv(name)
]
if _missing:
    sys.exit(
        f"Missing required env var(s): {', '.join(_missing)}.\n"
        "Add them to company-brain/.env before running the demo."
    )

client = anthropic.Anthropic()

# FlowBrain exposed as a tool Claude can call.
tools = [
    {
        "name": "get_company_workflow",
        "description": (
            "Retrieves Loopline's exact workflow and rules for handling any "
            "business situation. Always call this before taking any action "
            "that involves a company process — refunds, escalations, "
            "cancellations, onboarding, incidents. Returns structured steps, "
            "decision rules, required approvals, and exceptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "situation": {
                    "type": "string",
                    "description": (
                        "Natural language description of the situation you "
                        "need to handle"
                    ),
                }
            },
            "required": ["situation"],
        },
    }
]

SYSTEM_PROMPT = [
    {
        "type": "text",
        "text": (
            "You are a customer support agent for Loopline, a B2B SaaS "
            "company. Before handling any request involving a company "
            "process (refunds, cancellations, escalations, onboarding), you "
            "MUST call get_company_workflow to retrieve the exact process to "
            "follow. Never guess — always look up the workflow first. After "
            "getting the workflow, follow it precisely and explain your "
            "decision clearly to the customer."
        ),
        # Stable across all scenarios — cache the tools+system prefix.
        "cache_control": {"type": "ephemeral"},
    }
]

MAX_TURNS = 8


def call_flowbrain(situation: str) -> dict:
    """Call FlowBrain's /skills/match endpoint and return the matching workflow.

    On a non-200 (e.g. 404 SKILL_NOT_FOUND) returns an {"error": ...} dict so
    Claude sees the miss and can respond accordingly."""
    try:
        response = requests.get(
            f"{os.environ['FLOWBRAIN_API_URL'].rstrip('/')}/api/v1/skills/match",
            params={"q": situation},
            headers={"Authorization": f"Bearer {os.environ['FLOWBRAIN_API_KEY']}"},
            timeout=15,
        )
    except requests.RequestException as exc:
        return {"error": f"FlowBrain request failed: {exc}"}

    if response.status_code == 200:
        return response.json()
    # Surface the API's structured error body when there is one.
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return {"error": f"No workflow found (HTTP {response.status_code})", "detail": body}


def run_support_agent(customer_message: str, scenario_name: str) -> None:
    """Run the support agent for one customer message, printing the trace."""
    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'=' * 60}")
    print(f"Customer: {customer_message}")
    print(f"{'-' * 60}")

    messages = [{"role": "user", "content": customer_message}]
    last_skill = None  # the most recent workflow FlowBrain returned

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            # Preserve the full assistant turn (including thinking blocks +
            # signatures) so the follow-up request stays valid.
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                situation = block.input["situation"]
                print("\n[Agent calling FlowBrain]")
                print(f"Query: '{situation}'")

                workflow = call_flowbrain(situation)
                if "skill" in workflow:
                    last_skill = workflow["skill"]
                    print(f"\n[FlowBrain returned: '{last_skill['process']}']")
                    print(f"Confidence: {workflow.get('confidence', 'unknown')}")
                    print(f"Steps: {len(last_skill['steps'])}")
                    print(f"Decision rules: {len(last_skill['decision_rules'])}")
                    print(f"Approvals required: {len(last_skill['approvals'])}")
                else:
                    print(f"\n[FlowBrain returned no workflow: {workflow.get('error')}]")

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(workflow),
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # No tool call — Claude has produced its final answer.
        final_text = "\n".join(
            block.text for block in response.content if block.type == "text"
        )
        print("\n[Agent response]")
        print(final_text or "(no text response)")

        print("\n[FlowBrain rules applied]")
        if last_skill and last_skill.get("decision_rules"):
            for rule in last_skill["decision_rules"]:
                print(f"  - {rule}")
        else:
            print("  (none — no workflow was matched)")
        return

    print("\n[Agent stopped: hit the max turn limit without finishing]")


if __name__ == "__main__":
    print("FlowBrain Agent Demo")
    print("Proving AI agents follow company-specific rules")
    print("=" * 60)

    scenarios = [
        (
            "Hi, I bought your Pro plan 3 weeks ago and it's not working for "
            "my team. I'd like a refund please.",
            "Standard refund - under 30 days",
        ),
        (
            "I need a refund. I've been a customer for 8 weeks but your "
            "product completely broke our deployment pipeline last week. "
            "We're on your Enterprise plan.",
            "Edge case - over 30 days but Enterprise + defective",
        ),
        (
            "We need to cancel and get a full refund of our $2,400 annual "
            "payment. The product hasn't delivered what was promised.",
            "Escalation required - amount over $500",
        ),
    ]

    for message, name in scenarios:
        run_support_agent(message, name)

    print(f"\n{'=' * 60}")
    print("Demo complete.")
    print("FlowBrain gave the agent company-specific rules for every")
    print("scenario - including edge cases that generic AI would have")
    print("gotten wrong.")
    print("=" * 60)
