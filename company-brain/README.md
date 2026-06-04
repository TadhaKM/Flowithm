# Company Brain

A full-stack RAG system that ingests company knowledge (Slack, Notion, GitHub), embeds it into a Supabase vector store, and exposes a chat interface backed by Claude.

## Structure

```
company-brain/
├── ingest/        Python scripts for ingesting data sources (Slack, Notion, GitHub)
├── brain/         Python module for chunking, embedding, and storing
├── api/           FastAPI backend (query endpoint + skills file generation)
├── ui/            Next.js frontend (single-page chat interface)
├── demo-data/     Fake company data for the demo
└── ...
```

## Setup

### 1. Environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

You will need:
- `ANTHROPIC_API_KEY` — from https://console.anthropic.com (used for the chat model)
- `VOYAGE_API_KEY` — from https://dash.voyageai.com (used for embeddings)
- `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` — from your Supabase project settings

### 2. Python backend

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Database schema

In the Supabase SQL editor, run [brain/schema.sql](brain/schema.sql) once. This enables `pgvector`, creates the `chunks` and `skills` tables, the IVFFlat index, and the `match_chunks` similarity function.

### 4. Next.js frontend

```bash
cd ui
npm install
npm run dev
```

The UI will be available at http://localhost:3000. By default it talks to the API at `http://localhost:8000`; to point elsewhere, create `ui/.env.local` with `NEXT_PUBLIC_API_URL=...`.

### 5. Ingest demo data

```bash
python -m brain.run_ingest
```

This loads every source under `demo-data/`, embeds the chunks via Voyage `voyage-3`, and upserts them into the Supabase `chunks` table. Expect output like:

```
Embedded and stored 20/47 chunks...
Embedded and stored 40/47 chunks...
Embedded and stored 47/47 chunks...

Company Brain ingested: 22 slack chunks, 13 notion chunks, 5 github chunks
```

The individual scripts under `ingest/` can also be run standalone for debugging — each one prints its chunks as JSON to stdout without writing to the database.

### 6. Start the API

```bash
uvicorn api.main:app --reload
```

The API will be available at http://localhost:8000.

## Usage

Open the UI, ask a question, and the backend will retrieve relevant chunks from the vector store and answer with Claude.

## Agent API

Mount point: `/api/v1`. Auth: every call needs `Authorization: Bearer <fb_live_...>` — mint a key from the **Agent API** tab of the dashboard. A live OpenAPI spec is at `/api/v1/openapi.json`; Swagger UI is at `/api/v1/docs`.

Two endpoints make up the core agent surface:

| Endpoint | When to call it |
|---|---|
| `GET /api/v1/skills/match` | *"What is our process for X?"* — returns the workflow the agent should follow. |
| `POST /api/v1/skills/check` | *"Is this action allowed by our process?"* — returns whether the agent can proceed and what approvals are needed if not. |

Together: `match` tells the agent how to act correctly, `check` is a guardrail that stops it from acting incorrectly.

### `GET /api/v1/skills/match` — find the workflow

Semantic search over your skills. Top-10 raw candidates are re-ranked by a recency-weighted score (`similarity * 0.7 + recency * 0.3`) so a slightly less similar but more recently confirmed workflow beats a very similar but stale one.

```bash
curl "https://flowithm.io/api/v1/skills/match?q=customer+wants+a+refund+after+45+days" \
  -H "Authorization: Bearer fb_live_..."
```

```json
{
  "matched": true,
  "confidence": "high",
  "similarity_score": 0.83,
  "recency_score": 1.0,
  "combined_score": 0.881,
  "query": "customer wants a refund after 45 days",
  "skill": {
    "id": "...",
    "process": "Customer refund handling",
    "steps": [ /* ... */ ],
    "decision_rules": [ /* ... */ ],
    "approvals": [ /* ... */ ],
    "exceptions": [ /* ... */ ]
  },
  "last_confirmed_at": "2026-04-20T12:00:00+00:00",
  "days_since_confirmed": 25,
  "source_freshness": "fresh",
  "freshness_warning": null
}
```

Confidence tiers are computed from raw `similarity_score`: `high` (≥0.75), `medium` (0.40-0.75). Below 0.40 the endpoint returns `404 SKILL_NOT_FOUND` with up to 3 closest suggestions. If `source_freshness` is `stale`, surface `freshness_warning` to the operator — it's a verbatim escalation instruction written for humans.

### `POST /api/v1/skills/check` — guardrail before acting

Call this **before** any potentially destructive action. The endpoint finds the most relevant skill and asks Claude whether the proposed action follows the documented `decision_rules` / `approvals` / `exceptions`.

```bash
curl -X POST "https://flowithm.io/api/v1/skills/check" \
  -H "Authorization: Bearer fb_live_..." \
  -H "Content-Type: application/json" \
  -d '{
    "proposed_action": "Approve and process a $2400 refund immediately without escalation",
    "context": "Customer on Enterprise plan, claims product was defective, purchase was 8 weeks ago"
  }'
```

```json
{
  "allowed": false,
  "reason": "Refunds over $500 require CS lead approval before processing.",
  "required_approvals": [
    "CS lead via Slack DM with reason and Stripe charge ID"
  ],
  "violations": [],
  "suggested_action": "Route to CS lead for approval before processing the refund.",
  "matched_skill": { "id": "...", "process": "Customer refund handling" },
  "confidence": "high"
}
```

The endpoint **fails closed**: if no skill matches above the confidence floor, or Claude is unavailable, you get `allowed: false` with `confidence: "low"` and an escalation suggestion. An agent that surfaces `result["reason"]` and `result["suggested_action"]` verbatim never needs to know whether the guardrail blocked it because of a real policy or because the policy wasn't on file — the answer to both is *escalate*.

The Python pattern:

```python
import requests, os

response = requests.post(
    f"{os.environ['FLOWITHM_API_URL']}/api/v1/skills/check",
    json={
        "proposed_action": "approve $2400 refund",
        "context": "Enterprise customer, defective product",
    },
    headers={"Authorization": f"Bearer {os.environ['FLOWITHM_API_KEY']}"},
)
result = response.json()

if not result["allowed"]:
    escalate_to_human(result["suggested_action"], result["reason"])
else:
    proceed_with_action()
```

A runnable end-to-end demo using both endpoints with a real Claude agent lives at `demo/agent_demo.py`:

```bash
python demo/agent_demo.py
```

It walks through three customer refund scenarios (Claude calling `/skills/match` as a tool) and a fourth scenario showing the pre-action `/skills/check` guardrail.

## Connecting Gmail

Gmail is the highest-value source for capturing real process decisions —
escalations, exceptions, and edge cases that never made it into formal
documentation. Setup is one-time per account.

1. Create a Google Cloud project at <https://console.cloud.google.com>.
2. Enable the **Gmail API** for that project.
3. Create OAuth 2.0 credentials with application type **Desktop app**.
4. Download the resulting `client_secret.json` to your project root.
5. Add `GOOGLE_CLIENT_SECRET_PATH=client_secret.json` to your `.env`.
6. Run the one-shot bootstrap:
   ```bash
   python -m ingest.gmail_auth
   ```
   A browser window will open — sign in with the Gmail account you want
   Flowithm to read.
7. Open the generated `gmail_token.json` and copy its full contents.
8. In the dashboard, **Sources → + Connect source → Gmail**:
   - Paste the token contents into **Credentials JSON**.
   - Add a comma-separated list of **Label filters**, e.g. `process, policy, escalation, runbook`. Only threads with at least one of these labels get ingested.
   - Click **Connect**.

The scheduler will fetch matching threads on every cycle. Single-message
threads are skipped by default (see `min_thread_length`).

## Connecting Intercom

Intercom support conversations are where edge cases get decided in real
time. The ingestor focuses on closed conversations — preferably tagged
with something like `escalated` or `policy-question`.

1. In Intercom: **Settings → Developers → Your apps**. Create a new app
   (or pick an existing one).
2. Copy the **Access Token** from the app's Authentication tab.
3. (Recommended) Create an `escalated` tag in Intercom and apply it to
   conversations where unusual decisions were made. Flowithm will
   prioritise these.
4. In the dashboard, **Sources → + Connect source → Intercom**:
   - Paste the **Access token**.
   - Add comma-separated **Tags to watch** (optional — leave blank to
     ingest every closed conversation).
   - Set **Min message count** (default 3) to skip simple FAQ exchanges.
   - Click **Connect**.

The scheduler paginates through `/conversations/search` and pulls each
matching thread on every cycle.

## Verifying a connected source

After connecting, click **Sync now** on the Sources page. Watch the
FastAPI terminal for:

```
[Flowithm scheduler] cycle done — 12 new, 47 skipped, 0 errors, 14s
```

If the source row records errors instead, they appear in the
last-run banner above the source list — click **view logs** to expand.
