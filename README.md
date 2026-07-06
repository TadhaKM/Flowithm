<div align="center">

# Flowithm

**The runtime memory layer for AI agents.**

Turn the tribal knowledge buried in Slack threads, docs, and support tickets
into structured workflows that humans can follow and AI agents can execute.

[Quick start](#quick-start) ·
[How it works](#how-it-works) ·
[Agent API](#the-agent-api) ·
[Architecture](#architecture) ·
[Docs](#documentation)

</div>

---

## What is Flowithm?

Your company's best processes don't live in the wiki. They live in a Slack
thread from eight months ago, a support escalation that set a precedent, and
the head of the one person who "just knows how we do refunds."

Flowithm captures that knowledge and keeps it honest:

- **Ingests** from the tools where decisions actually happen — Slack, Notion,
  GitHub, Gmail, and Intercom — on a continuous schedule.
- **Extracts** real workflows: triggers, ordered steps, owners, decision
  rules, required approvals, and exception paths. Not a summary — a spec.
- **Exposes** everything through a public Agent API, so your AI agents can ask
  *"what's our process for X?"* and *"am I allowed to do this?"* at runtime.
- **Stays fresh** with three background loops: continuous ingestion, drift
  detection (Claude flags contradictions between new content and existing
  workflows), and staleness flagging (unreviewed workflows get marked so
  agents escalate instead of auto-executing).

Two questions sit at the core of the product, and every agent should ask them
before acting:

| Question | Endpoint |
|---|---|
| *"What's the workflow for this situation?"* | `GET /api/v1/skills/match` |
| *"Is this action allowed by our process?"* | `POST /api/v1/skills/check` |

`match` tells the agent how to act correctly; `check` is the guardrail that
stops it from acting incorrectly — and it **fails closed**.

## How it works

```
   Slack · Notion · GitHub · Gmail · Intercom
                      │
                      ▼
        ┌─────────────────────────────┐
        │   Continuous ingestion      │  scheduler pulls incrementally,
        │   chunk → embed → dedup     │  SHA-256 dedup, per-org cycles
        └─────────────┬───────────────┘
                      ▼
        ┌─────────────────────────────┐
        │   Knowledge base            │  Supabase Postgres + pgvector,
        │   chunks + skills           │  Voyage voyage-3 embeddings
        └─────┬───────────────┬───────┘
              ▼               ▼
   ┌─────────────────┐  ┌──────────────────┐
   │ Drift detection │  │ Staleness pass   │   conflicts surface as
   │ (Claude)        │  │ (needs_review)   │   reviewable cards
   └────────┬────────┘  └────────┬─────────┘
            ▼                    ▼
   ┌──────────────────────────────────────┐
   │  Dashboard (Next.js)  +  Agent API   │
   │  humans review          agents query │
   └──────────────────────────────────────┘
```

1. **Connect sources** from the dashboard. The scheduler fetches new content
   every cycle, chunks it, embeds it, and de-duplicates by content hash.
2. **Generate workflows** — paste a thread or let Flowithm extract from
   ingested content. Claude produces structured JSON: steps, owners,
   decision rules, approvals, exceptions, and source citations.
3. **Agents query at runtime** via the Agent API with per-org scoped,
   bcrypt-hashed API keys. Every response carries freshness metadata
   (`needs_review`, `source_freshness`, `days_since_confirmed`) so agents
   know when to escalate to a human instead of acting.

## Tech stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI (Python), APScheduler for ingestion cycles |
| Models | Claude (extraction, drift detection, guardrail checks), Voyage `voyage-3` (embeddings) |
| Database | Supabase Postgres with `pgvector` + `pg_trgm`, Supabase Auth |
| Dashboard | Next.js (App Router) + Tailwind CSS |
| Slack bot | Bolt (Socket Mode) — extracts workflows from threads on demand |
| Deploy | Railway (API) + Vercel (dashboard) + Supabase — see [DEPLOYMENT.md](company-brain/DEPLOYMENT.md) |

## Quick start

Everything lives under [`company-brain/`](company-brain/). You'll need Python
3.11+, Node 20+, a Supabase project, and API keys for
[Anthropic](https://console.anthropic.com) and
[Voyage](https://dash.voyageai.com).

```bash
cd company-brain

# 1. Configure credentials
cp .env.example .env          # fill in Anthropic, Voyage, Supabase keys

# 2. Python backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Database — run brain/schema.sql in the Supabase SQL editor (idempotent)

# 4. Ingest the bundled demo data
python -m brain.run_ingest

# 5. Start the API
uvicorn api.main:app --reload          # http://localhost:8000

# 6. Start the dashboard (separate terminal)
cd ui && npm install && npm run dev    # http://localhost:3000
```

Sign up at `http://localhost:3000/signup`, connect a source (or paste a
thread), and generate your first workflow. The full walkthrough — including
Gmail and Intercom source setup — is in
[company-brain/README.md](company-brain/README.md).

## The Agent API

Mounted at `/api/v1` with Bearer-token auth (mint keys from the dashboard's
**Agent API** tab). Live Swagger UI at `/api/v1/docs`.

**Find the workflow before acting:**

```bash
curl "https://your-host/api/v1/skills/match?q=customer+wants+a+refund+after+45+days" \
  -H "Authorization: Bearer fb_live_..."
```

Results are re-ranked by `similarity × 0.7 + recency × 0.3`, so a recently
confirmed workflow beats a similar-but-stale one. Below the confidence floor
you get a 404 with the closest suggestions instead of a bad guess.

**Guardrail-check a proposed action:**

```python
result = requests.post(
    f"{API_URL}/api/v1/skills/check",
    json={
        "proposed_action": "approve $2400 refund",
        "context": "Enterprise customer, defective product",
    },
    headers={"Authorization": f"Bearer {API_KEY}"},
).json()

if not result["allowed"]:
    escalate_to_human(result["suggested_action"], result["reason"])
```

If no matching policy exists — or the model is unavailable — the check
returns `allowed: false` with an escalation suggestion. The agent never has
to distinguish "blocked by policy" from "policy not on file": both mean
escalate.

A runnable end-to-end demo (a Claude agent handling refund scenarios with
`match` as a tool and `check` as a pre-action guardrail) lives at
[`company-brain/demo/agent_demo.py`](company-brain/demo/agent_demo.py).

## Architecture

```
company-brain/
├── api/           FastAPI — internal app + /api/v1 public agent API
├── brain/         Domain logic: embedder, store, drift, scheduler, staleness
├── ingest/        One ingestor per source (slack, notion, github, gmail, intercom)
├── slack/         Slack bot (Bolt) — interactive workflow extraction
├── ui/            Next.js dashboard — generator, knowledge base, sources, API keys
├── tests/         pytest suite (232 tests, no live services required)
├── demo/          Runnable agent demo against the public API
├── demo-data/     Sample source material for the offline demo path
├── PROJECT.md     Deep-dive reference: every module, table, and endpoint
└── DEPLOYMENT.md  Railway + Vercel + Supabase production guide
```

Security is built in rather than bolted on: per-org data isolation on every
query, bcrypt-hashed API keys with sliding-window rate limits, HMAC-signed
admin requests (`X-Admin-Sig`) with a dedicated signing key, AES-256-GCM
encryption for stored source credentials, and bounded inputs on every public
endpoint.

## Testing

```bash
cd company-brain
make test              # 232 tests, fully mocked — no live services needed
make test-coverage     # HTML coverage report
```

## Documentation

| Document | What's inside |
|---|---|
| [company-brain/README.md](company-brain/README.md) | Full setup walkthrough, Agent API reference, Gmail/Intercom source guides |
| [company-brain/PROJECT.md](company-brain/PROJECT.md) | Living deep-dive: every backend module, DB table, route, and the build history |
| [company-brain/DEPLOYMENT.md](company-brain/DEPLOYMENT.md) | Production deployment on Railway + Vercel + Supabase, secret rotation, smoke tests |

## License

All rights reserved.
