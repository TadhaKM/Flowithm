# Flowithm — Project Reference

A living document covering what Flowithm is, what's been built, and how
the pieces fit together. Updated alongside every meaningful code change
so the bottom of the file is always a faithful changelog.

---

## What Flowithm is

Flowithm is the **runtime memory layer for AI agents**. It captures how
a company actually does things — not the polished version in the wiki,
but the real version that lives in Slack threads, Notion pages, GitHub
issues, support emails, and Intercom conversations — and exposes it as
structured workflows that any agent can query at runtime.

The product centre of gravity is two questions every agent needs answered
before acting on company data:

1. *"What's the workflow for this situation?"* → `GET /api/v1/skills/match`
2. *"Has this workflow drifted? Should I trust it?"* → `needs_review`
   flag on every response

Around that core, Flowithm runs **three continuous background loops** that
keep the knowledge fresh:

- **Continuous ingestion** — a scheduler pulls from every connected
  source on a configurable cadence and de-duplicates content via
  SHA-256 of the chunk body.
- **Drift detection** — every new workflow (and every newly-ingested
  chunk) is compared against existing skills via Claude; genuine
  contradictions surface as reviewable conflict cards.
- **Staleness flagging** — skills that haven't been reviewed within
  `STALE_THRESHOLD_DAYS` get auto-flagged so agents know to escalate
  rather than auto-execute.

The end result: an evergreen knowledge base + a public agent API + a
dashboard for humans to keep both honest.

---

## Repository layout

```
company-brain/
├── api/             FastAPI: internal app + /api/v1 sub-app for the public agent API
├── brain/           Domain logic — embedder, store, drift, scheduler, staleness, etc.
├── ingest/          One module per source type (slack, notion, github, gmail, intercom, pdfs)
├── slack/           Slack bot (Bolt) — separate from the Slack ingestor
├── ui/              Next.js dashboard (App Router, Tailwind, dark theme)
├── tests/           pytest suite (118 tests, no live services required)
├── demo-data/       Sample source material for the offline demo path
├── PROJECT.md       This file
├── README.md        Setup + usage walkthrough
├── requirements.txt
└── .env.example
```

The `slack/` directory and the `ingest/ingest_slack.py` module are
intentionally separate — `slack/` is the bot that **listens** to events
and triggers workflow extraction interactively, `ingest/ingest_slack.py`
is the scheduler-driven ingestor that **fetches** message history.

---

## Backend modules

### `brain/`

| Module | Purpose |
|---|---|
| `text_utils.py` | Owns the `cl100k_base` tiktoken encoder. `count_tokens()`, `cap_tokens(strategy="truncate" \| "middle_out" \| "smart")` — the single canonical token-budget helper used everywhere. |
| `logger.py` | `get_logger(name)` returns a stdlib logger configured with a JSON formatter writing to stdout. Reserved keys (`org_id`, `duration_ms`, `request_id`, `status_code`, `endpoint`) get top-level keys; the rest of `extra={...}` lands under `"extra"`. `LOG_LEVEL` env var overrides the default INFO. |
| `anthropic_client.py` | `messages_create(client, **kwargs)` wraps `client.messages.create` with retry (3× exp backoff on 429/5xx/connection errors), per-call timeout (`ANTHROPIC_TIMEOUT_SECONDS`, default 60), and a process-wide circuit breaker that opens after 5 consecutive failures and stays open 60s. `CircuitOpenError` lets callers degrade gracefully. |
| `chunker.py` | `chunk_text()` — splits a long string into overlapping ~600-token chunks. Pulls its encoder from `text_utils` so there's exactly one cl100k_base instance. |
| `ingestors.py` | `BaseIngestor` ABC + `Chunk` dataclass shared by every concrete ingestor. `process()` does build → validate → log filtering; `validate()` drops short or null-source chunks. |
| `embedder.py` | Voyage `voyage-3` embeddings + Supabase chunk storage. Public surface: `get_embedding`, `get_embeddings_batch`, `chunk_exists`, `store_chunk`, `embed_and_store`, `embed_query`. SHA-256 dedup on `chunks.content_hash`. |
| `store.py` | Every Supabase read/write outside of the embedder. Skills, conflicts, api_keys, api_requests, executions, connected_sources, ingest_runs, **organisations**. `_row_to_workflow()` is the canonical DB-row → JSON shape mapper. Every helper accepts an optional `org_id: str \| None`; `_default_org_id()` falls back to the `ORG_ID` env so existing callers keep working. |
| `query.py` | Claude generation: `query_brain()` (RAG Q&A), `generate_skills_file()` (skill JSON from chunks), `generate_workflow_from_text()` (skill JSON from pasted text). After every `generate_workflow_from_text`, embeds the result's summary text and schedules a drift check. |
| `drift.py` | Two entry points: `check_for_drift(content, new_skill)` for newly-generated workflows; `check_chunks_against_skills(chunks)` for incoming raw content. `resolve_conflict(id, action)` handles `accept` (rewrites + versions + cascades siblings), `dismiss`, `snooze`. `get_unresolved_conflicts` + `get_conflict_history` feed the UI. |
| `scheduler.py` | `IngestionScheduler` singleton driven by APScheduler. `run_ingest_cycle` groups every active source by `org_id` and runs an independent sub-cycle per organisation: fetch → embed → drift → staleness → write `ingest_runs` row. Wrapped in a row-mutex so multi-worker uvicorn deployments can't race. |
| `staleness.py` | `run_staleness_check()` flags skills older than `STALE_THRESHOLD_DAYS` without recent review; `mark_as_reviewed(id)` clears the flag and bumps `reviewed_at`. |
| `backfill_embeddings.py` | One-shot CLI: embeds every existing skill row missing a `summary_embedding`. Idempotent. |
| `test_drift.py` | Manual verification script (NOT pytest). Round-trips drift end-to-end against live Supabase + Anthropic. |
| `schema.sql` | The single source of truth for the database schema. Re-runnable. |

### `ingest/`

Every concrete ingestor inherits `BaseIngestor`, accepts no constructor
args for the demo path, and accepts source-specific live-mode params
(token, ids, since) when called by the scheduler.

| Module | Sources | Live mode |
|---|---|---|
| `ingest_slack.py` | Slack channels | `conversations.history` with `oldest=since.timestamp()` per channel; pulls thread replies; rate-limited 0.5s/page |
| `ingest_notion.py` | Notion pages | `GET /v1/pages/{id}` for incremental check via `last_edited_time`; recursive `GET /v1/blocks/{id}/children` walk → markdown; ~3 req/s rate limit |
| `ingest_github.py` | GitHub issues | Demo only (lives off `github_issues.json`) |
| `ingest_gmail.py` | Gmail threads | `users.threads.list` with `q=label:NAME after:UNIX_TIMESTAMP`; per-thread full fetch + base64url body decode; multipart-aware. **Optional google-* deps imported lazily.** |
| `ingest_intercom.py` | Intercom conversations | `POST /conversations/search` (paginated) → per-conversation full GET with `display_as=plaintext`; defensive HTML strip; closed-state filter. |
| `gmail_auth.py` | One-shot CLI to mint OAuth credentials | `python -m ingest.gmail_auth` |
| `ingest_pdfs.py` | PDF + .txt files | Demo only |

### `api/`

| Module | Purpose |
|---|---|
| `main.py` | Internal FastAPI app. Lifespan starts/stops the scheduler. Routes for `/query`, `/skills`, `/workflows/{generate,similar,id,id/archive}`, `/history`, `/conflicts`, `/skills/{id}/conflicts`, `/skills/{id}/review`, `/ingest/{status,trigger}`, `/sources` CRUD, `/demo/{slug}`, `/health`. |
| `agent.py` | Public agent API mounted at `/api/v1`. Sub-app with its own OpenAPI at `/api/v1/openapi.json` and Swagger UI at `/api/v1/docs`. Endpoints: `POST/GET /keys`, `DELETE /keys/{id}` (admin-gated), `GET /skills`, `GET /skills/{name}`, `GET /skills/match`, `POST /skills/execute`. Standard `{error, code, docs}` envelope on every error. |
| `auth.py` | `verify_api_key` FastAPI dependency: Bearer extraction → prefix-indexed candidate lookup (cross-org) → bcrypt verify → active check → 100/min sliding-window rate limit → BackgroundTask audit log. The matched key's `org_id` lands on `request.state.org_id` for downstream use. `verify_admin_token` for the key-management endpoints. |
| `main.py` `get_org_id(request)` | Resolves the request's tenant: `X-Org-ID` header → `ORG_ID` env → seed default UUID. Wired as `_OrgDep` on every internal endpoint that touches a tenant-scoped table. |

### `slack/`

The Slack bot — completely separate from the ingestor. Listens for
trigger phrases in messages, posts a confirmation, lets a user click
*Extract*, then runs the `/workflows/generate` flow against the thread
text. Hooks into drift detection and posts a follow-up message in the
same thread when conflicts are found. Uses `FLOWITHM_URL` /
`FLOWITHM_API_URL` env vars to build deeplinks.

---

## Frontend pages (`ui/app/`)

The dashboard is a Next.js 16 App Router project with Tailwind v3.
Brand colour `#1D9E75` (teal); dark theme using zinc neutrals.

| Route | Purpose |
|---|---|
| `/` | Workflow generator. Paste source material, name it, click Generate → calls `/workflows/generate`. Two-panel output (workflow + skills file JSON). Recent workflows row at the bottom with *Clear all*. |
| `/brain` | Knowledge-base dashboard. 5-card metric row (workflows, sources, last updated, last synced, **needs review**). Conflicts banner + section with severity-coded cards (two-column diff, Accept/Dismiss/Snooze). Workflow grid/list with search, source filter, sort, view toggle. Each workflow card has a kebab menu (Copy JSON, Mark as reviewed, Archive). |
| `/brain/[id]` | Workflow detail. Two-panel render. Header has Edit name / Re-extract / Mark as reviewed / Archive. **Staleness banner** above the panels when `needs_review` is true. |
| `/brain/api` | Agent API tab. Keys management (table + two-click revoke + new-key modal showing plaintext once with copy button). 30-day usage stats (cards + 14-day local-time SVG bar chart). Three integration snippets (TS / Python / Claude tool use) with the `needs_review` escalation pattern in each. Live `/skills/match` playground via server-side playground key with syntax-highlighted JSON response. |
| `/brain/sources` | Connected-sources dashboard. Last-run banner with new-chunks / skipped / conflicts / staleness counts and inline-expandable error logs. Per-source cards (type icon, name, active/paused toggle, last_synced, two-click Remove). + Connect source modal with per-type fields (Slack / Notion / **Gmail** / **Intercom** / GitHub). |
| `/workflow/[id]` | Slack-bot deeplink target. Read-only two-panel render with Copy JSON. |
| `/setup` | First-run organisation bootstrap. Renders when no `flowithm_org_id` cookie is present (middleware redirects). Submits `{company_name, user_name?}` to `/api/setup`, which creates the org and sets the httpOnly cookie. Single-tenant deploys can skip the gate by setting `FLOWITHM_DEFAULT_ORG_ID` in the dashboard env. |

### Server-only proxy routes (`ui/app/api/`)

The dashboard never embeds the Supabase service key, FastAPI
`ADMIN_TOKEN`, or playground key in the browser bundle. Every admin call
goes through a Next.js route that injects the secret AND the
`flowithm_org_id` cookie value as `X-Org-ID` (via `lib/org.orgHeaders`).

- `/api/setup` — POST → FastAPI `/setup`, sets the org cookie
- `/api/brain` — list workflows (Supabase direct, scoped to org_id)
- `/api/brain/[id]` — GET / PATCH single workflow (scoped to org_id)
- `/api/brain/[id]/review` — POST → FastAPI `/skills/{id}/review`
- `/api/conflicts` — GET → FastAPI `/conflicts`
- `/api/conflicts/[id]/resolve` — POST → FastAPI `/conflicts/{id}/resolve`
- `/api/admin/keys` — GET/POST → FastAPI `/api/v1/keys` (with `ADMIN_TOKEN`); POST injects `org_id` from cookie into the body
- `/api/admin/keys/[id]` — DELETE → FastAPI `/api/v1/keys/{id}`
- `/api/admin/usage` — Supabase aggregations for the dashboard
- `/api/admin/playground` — GET → FastAPI `/api/v1/skills/match` (with `FLOWITHM_PLAYGROUND_KEY`); the playground key carries its own org_id
- `/api/admin/sources` + `[id]` — CRUD proxies for `/sources`
- `/api/admin/ingest` — GET status + POST trigger

`ui/middleware.ts` redirects every non-API page to `/setup` when no
`flowithm_org_id` cookie is present (and `FLOWITHM_DEFAULT_ORG_ID` env
isn't set as a single-tenant escape hatch).

---

## Database schema

Postgres on Supabase, with `vector` (pgvector) and `pg_trgm` extensions.

| Table | Purpose |
|---|---|
| `organisations` | Multi-tenancy root. Every domain row references this. Bootstrap seeds a default row at UUID `00000000-0000-0000-0000-000000000001` for self-hosted single-tenant deploys. |
| `chunks` | One row per embedded text chunk. `embedding vector(1024)` (Voyage `voyage-3`). `content_hash` SHA-256 unique index dedups re-ingested content. `org_id` ties to `organisations`. |
| `skills` | Generated workflow records. `summary_embedding vector(1024)` powers `/api/v1/skills/match`. `version` + `previous_version_id` track drift accepts. `needs_review` + `needs_review_reason` + `stale_flagged_at` for staleness. `archived` + `archived_at` for soft-delete. `org_id` ties to `organisations`. |
| `conflicts` | Drift records — contradiction / update / expansion / deprecation. Status: unresolved / accepted / dismissed / snoozed. `resolved_by` + `resolved_at` + `snoozed_until`. `org_id`. |
| `api_keys` | Public agent API auth. `key_hash` (bcrypt), `key_prefix` (indexed for candidate lookup), `request_count`, `last_used_at`, `is_active`. `org_id` resolved at auth time and propagated to every downstream query. |
| `api_requests` | Per-call audit log written from BackgroundTasks. Includes endpoint, query, matched_skill_id, response_time_ms. |
| `executions` | Agent feedback loop — `outcome ∈ {completed, escalated, exception_triggered}` + optional `exception_note` (triggers a drift check via Claude judgement). `org_id`. |
| `connected_sources` | Per-tenant ingest source configs. `source_type` + jsonb `config` (tokens stored server-side; redacted in API responses). `org_id`. |
| `ingest_runs` | One row per scheduled or manual run. `new_chunks`, `skipped_chunks`, `new_conflicts`, `stale_flagged`, `stale_cleared`, `errors` jsonb array. `org_id`. |
| `ingest_lock` | Singleton row-mutex. `try_acquire_ingest_lock(holder)` / `release_ingest_lock()`. 15-min stale-lock timeout for self-healing. **Global** — not per-org. |

### RPC functions

| Function | Used by |
|---|---|
| `match_chunks(query_embedding, match_count, target_org_id)` | `query_brain` (RAG Q&A); `target_org_id` filters per tenant |
| `match_skills(query_embedding, match_count, target_org_id)` | `/api/v1/skills/match` (returns `needs_review` + `needs_review_reason`) |
| `find_similar_workflow(q_name, min_sim, exclude_id, target_org_id)` | Slack bot's "Update existing" detection; `get_skill_by_name_fuzzy` |
| `increment_api_key_usage(key_id)` | Per-request atomic counter bump |
| `try_acquire_ingest_lock(holder)` / `release_ingest_lock()` | Multi-worker mutex around `run_ingest_cycle` |

---

## Continuous loops

### Ingestion cycle (`brain/scheduler.py`)

Triggered every `INGEST_SCHEDULE_HOURS` (default 24) by APScheduler, OR
manually via `POST /ingest/trigger` (admin-only).

```
1. Acquire ingest_lock (row-mutex) → if held, log + return
2. For each active connected_source:
     a. Lazy-import the matching ingestor (slack/notion/gmail/intercom)
     b. Fetch incrementally (since = source.last_synced_at)
     c. Validate + chunk
     d. embed_and_store(chunk) per chunk → returns uuid OR None (dedup hit)
     e. Update connected_sources.last_synced_at
3. Run check_chunks_against_skills(newly_embedded) → conflicts
4. Run run_staleness_check() → flag/clear needs_review
5. Insert ingest_runs row with the full summary
6. Release ingest_lock
```

Failures per step land in `results['errors']` rather than crashing the
cycle. The summary appears in `/brain/sources` last-run banner and via
`GET /ingest/status`.

### Drift detection (`brain/drift.py`)

Two entry points, both backed by Claude Sonnet 4.6 with structured JSON
output:

- `check_for_drift(content, new_skill)` — fired after every successful
  `generate_workflow_from_text` (UI + Slack bot). Compares the new
  skill against existing non-archived peers via on-the-fly Voyage
  similarity → top-20 candidates → single Claude call.
- `check_chunks_against_skills(chunks)` — fired by the scheduler over
  newly-embedded chunks. For each chunk, embeds, finds top-2 most
  similar skills above 0.45 cosine, asks Claude per pair: "does this
  contradict?". Only `is_conflict=true` responses persist as conflict
  rows.

When the user accepts a conflict, Claude rewrites the existing skill
per the suggested update, the new row gets `version + 1` and
`previous_version_id`, the old row is archived, and any sibling
conflicts targeting the same archived skill cascade-update to point at
the new row.

### Staleness pass (`brain/staleness.py`)

Runs at the end of every ingest cycle. Walks every non-archived skill;
flags those that are either:
- never reviewed AND created > `STALE_THRESHOLD_DAYS` ago, OR
- last reviewed > `STALE_THRESHOLD_DAYS` ago.

Cleared when the user clicks *Mark as reviewed* (UI or
`POST /skills/{id}/review`). Flag surfaces on workflow cards (badge),
the workflow detail page (banner), the metrics row (clickable filter),
and every `/api/v1/skills*` agent response (`needs_review` field +
`needs_review_reason`).

---

## Public Agent API surface

Mounted at `/api/v1`. Bearer-token auth on every endpoint. Standard
`{error, code, docs}` envelope on errors.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/keys` | Mint a key (admin-only). Plaintext returned **once**. |
| GET | `/api/v1/keys` | List keys (admin-only). Plaintext never serialised. |
| DELETE | `/api/v1/keys/{id}` | Revoke (admin-only). Soft-delete. |
| GET | `/api/v1/skills` | Paginated index. Filters: `?source`, `?updated_after`, `?needs_review`, `?limit`, `?offset`. |
| GET | `/api/v1/skills/match?q=...` | Semantic match via voyage-3 + match_skills RPC. Confidence tiers: high (≥0.75), medium (0.40-0.75). 404 with closest-3 below 0.40. |
| GET | `/api/v1/skills/{name}` | Exact-match (case-insensitive) → pg_trgm fuzzy fallback at 0.4. 404 with `closest_match` above 0.2. |
| POST | `/api/v1/skills/execute` | Agent feedback. `{skill_id, step_number, outcome, exception_note?, duration_seconds?}`. If `exception_note` is supplied, Claude haiku judges whether it's already covered → if NO, fires a drift check. |

Rate limit: 100 req/min per key, sliding window, in-memory.
`x-flowithm-elapsed-ms` not yet emitted (the dashboard's playground
proxy times round-trip itself).

### Error codes

`MISSING_API_KEY`, `INVALID_API_KEY`, `REVOKED_API_KEY`,
`RATE_LIMIT_EXCEEDED`, `SKILL_NOT_FOUND`, `INVALID_REQUEST`,
`INTERNAL_ERROR`.

---

## Configuration reference

### `.env` (FastAPI side)

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. Claude Sonnet 4.6 + Haiku 4.5. |
| `VOYAGE_API_KEY` | — | Required. Voyage `voyage-3` embeddings (1024-dim). |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | — | Required. Server-side Supabase access. |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_APP_TOKEN` | — | Slack bot only. |
| `FLOWITHM_URL` | `http://localhost:3000` | Dashboard host. Used by Slack bot for deeplinks. |
| `FLOWITHM_API_URL` | `http://localhost:8000` | FastAPI host. Used by Slack bot + Next.js proxies. |
| `ADMIN_TOKEN` | — | **Required for `/api/v1/keys` + `/sources` mutations.** Generate with `secrets.token_urlsafe(32)`. |
| `INGEST_SCHEDULE_HOURS` | `24` | Scheduler cadence. |
| `STALE_THRESHOLD_DAYS` | `90` | Threshold for `run_staleness_check`. Read at call time. |
| `GOOGLE_CLIENT_SECRET_PATH` | `client_secret.json` | One-shot Gmail OAuth bootstrap only. |
| `ORG_ID` | `00000000-0000-0000-0000-000000000001` | Default tenant for self-hosted single-org deploys. Used as a fallback when no `X-Org-ID` header is present. Matches the seed row in `schema.sql`. |
| `LOG_LEVEL` | `INFO` | Min level for `brain/logger.get_logger` loggers (DEBUG / INFO / WARNING / ERROR). |
| `ANTHROPIC_TIMEOUT_SECONDS` | `60` | Per-call timeout for `brain/anthropic_client.messages_create`. Bump if generate_workflow_from_text runs long with adaptive thinking. |
| `APP_VERSION` | `dev` | Surfaced by `GET /health` so probes can confirm which build is running. Set this in the deploy pipeline. |

### `ui/.env.local` (Next.js side)

Must be set independently — Next.js doesn't read the parent `.env`.
Mirror `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ADMIN_TOKEN`,
`FLOWITHM_API_URL`, plus `FLOWITHM_PLAYGROUND_KEY` (a key minted via
`POST /api/v1/keys` named `Playground`).

---

## Test coverage

`pytest` suite under `tests/`. **118 tests, all passing**, no live
services required:

- `test_chunker.py` — text splitting + overlap
- `test_query_schemas.py` — SKILL_SCHEMA + WORKFLOW_SCHEMA shape
- `test_store_mapping.py` — `_row_to_workflow` mapping
- `test_ingest.py` — Slack/Notion/GitHub chunk-builder logic
- `test_slack_handlers.py` — trigger regex, doc URL extraction, helpers
- `test_slack_formatter.py` — Block Kit rendering
- `test_api_routes.py` — every internal endpoint via TestClient with
  monkeypatched dependencies
- `test_smoke.py` — every top-level module imports cleanly

**Not yet covered:** `brain/drift.py`, `brain/scheduler.py`,
`brain/staleness.py`, `api/agent.py`, `api/auth.py`, the new ingestors.
The user has explicitly deferred adding tests for these until before
deployment.

---

## Build history

Chronological — most recent at the bottom. Commit hashes are
authoritative; full message bodies via `git show <hash>`.

| Hash | Title |
|---|---|
| `4b27d24` | Starting Prototype |
| `716ed51` | Build Flowithm workflow generator end-to-end |
| `4432359` | Add Slack bot, knowledge-base dashboard, and workflow deeplinks |
| `6ed7436` | Rename FlowBrain -> Flowithm; add backend test suite (118 tests) |
| `9b02539` | Add drift detection, public agent API, dashboards, and ingestion refactors |
| `9b86ad6` | Add live-mode constructors to Slack and Notion ingestors |
| `ef9e83d` | Wire scheduler into FastAPI lifespan + add ingest/sources endpoints |
| `d4e365c` | Add sources dashboard, last-run banner, and 'Last synced' metric card |
| `d2ec2f9` | Implement NotionIngestor live fetch via Notion REST API |
| `7bd9e9c` | Add `check_chunks_against_skills` + wire into scheduler |
| `ab1c963` | Multi-worker safety: row-mutex around `run_ingest_cycle` |
| `29a0ed3` | Add staleness detection: needs_review flag + scheduler hook + endpoints |
| `521ed9e` | Surface staleness in the dashboard: badges, banner, metric, snippets |
| `b53ab63` | Add Gmail and Intercom ingestors + scheduler dispatch + API validation |
| `7d75deb` | Connect-source modal: Gmail + Intercom field configurations + README docs |
| `66b31e9` | Note Gmail label-quoting follow-up as a TODO |
| `3e6d8e0` | Add PROJECT.md — comprehensive project reference |
| `813fe4b` | Multi-tenancy schema: `organisations` table + `org_id` columns + backfill + RLS enable + RPC `target_org_id` filter (commit 1 of 3) |
| `8c60168` | Multi-tenancy backend wiring: every store helper takes `org_id`, scheduler runs per-org, auth extracts org from API key, `POST /setup` endpoint, `X-Org-ID` header on Slack bot calls (commit 2 of 3) |
| `4cc4616` | Multi-tenancy dashboard: `/setup` page, `flowithm_org_id` httpOnly cookie, Next.js middleware redirect, `lib/org.ts` helper, every proxy route forwards `X-Org-ID` (commit 3 of 3) |
| `0195ed9` | Backfill commit hash in PROJECT.md build history |
| `1febad9` | Structured JSON logger (`brain/logger.py`) + global FastAPI exception handlers; `print()` calls in scheduler / drift / embedder / staleness / query / api replaced with structured logs (commit 1 of 3) |
| `3540708` | Anthropic retry wrapper + circuit breaker (`brain/anthropic_client.py`); every direct `messages.create` call swapped to `messages_create()` (commit 2 of 3) |
| `3835def` | Real `/health` probe (Supabase + Anthropic + Voyage + scheduler + circuit-breaker checks) + `APP_VERSION` env (commit 3 of 3) |

---

## Known limitations / explicitly deferred

These are intentional choices, flagged in commit messages or this
session — not bugs.

- **NotionIngestor doesn't render tables.** Notion's `table` →
  `table_row` block tree adds non-trivial complexity and downstream
  consumption is mixed. Skipped until needed.
- **Gmail label names with spaces** require quoting in the `q=` param.
  TODO comment in `ingest_gmail.py`; will fix when first reported.
- **Intercom HTML stripping** is a regex pass, not a full HTML
  parser. Defensive only — covers Intercom's standard plaintext output.
- **Rate limiter is in-memory** (per-process). Multi-worker uvicorn
  with `--workers=N` would give each worker its own counter. Single
  worker is fine; multi-worker would need Redis.
- **`POST /api/v1/keys` is admin-gated by `ADMIN_TOKEN`** but exposed
  on the same host as the public API. Acceptable on localhost; a real
  deployment should also restrict by network (VPN, IP allowlist).
- **No automated tests for drift / scheduler / agent API yet.** Manual
  verification via `python -m brain.test_drift` and curl smoke tests.
- **The agent SDK doesn't exist yet.** Right now agents speak the API
  directly — no language-specific helper packages.

---

## Maintenance rule

**Every commit that adds, removes, or meaningfully changes behaviour
must update this file in the same commit.** That keeps the file
trustworthy as an entry point for someone (or a future-self agent)
joining the project cold. The bottom-of-file `Build history` table is
the minimum touch; `Backend modules`, `Frontend pages`, `Database
schema`, `Public Agent API surface`, and `Known limitations` get
updated when those areas change.
