# Deployment checklist

Production target: **Railway** for the FastAPI backend, **Vercel** for
the Next.js dashboard, **Supabase** for Postgres + Auth. Anthropic +
Voyage for the model providers.

> [!NOTE]
> The Slack bot (`slack/app.py`) runs Socket Mode — it doesn't expose a
> port, so Railway's web service won't run it. Deploy it as a separate
> Railway service or worker, or run it on your own host.

---

## 0. Rotate secrets (one-time)

The `.env` file was exposed in a prior conversation. **Rotate now** before
deploying:

| Secret | Where to rotate |
|--------|----------------|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys → revoke + mint |
| `VOYAGE_API_KEY` | dash.voyageai.com → API Keys |
| `SUPABASE_SERVICE_KEY` | Supabase Dashboard → Settings → API → Reset service_role |
| `ADMIN_TOKEN` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `BOOTSTRAP_TOKEN` | Same: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ENCRYPTION_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |

Update `.env` and `ui/.env.local` with the new values **before** step 2.

---

## 1. Supabase — schema + Auth

### Schema

Open the Supabase SQL editor and run
[`brain/schema.sql`](brain/schema.sql) **end to end**. It's idempotent —
re-runnable for every subsequent migration.

After it completes, verify the tables exist:

```sql
select table_name
from   information_schema.tables
where  table_schema = 'public'
order  by table_name;
```

Expected: `api_keys`, `api_requests`, `chunks`, `conflicts`,
`connected_sources`, `executions`, `ingest_lock`, `ingest_runs`,
`organisations`, `skills`, `users`.

### Auth

1. Supabase Dashboard → Authentication → Providers → confirm **Email** is enabled.
2. Authentication → URL Configuration → set **Site URL** to your Vercel deploy URL.
3. Note down these two values from Settings → API:
   - **Project URL** → goes into `NEXT_PUBLIC_SUPABASE_URL`
   - **anon/public key** → goes into `NEXT_PUBLIC_SUPABASE_ANON_KEY`

---

## 2. Railway — FastAPI backend

The repository's Python project lives at `company-brain/`. When creating
the Railway service, set **rootDirectory** to `company-brain` so it picks
up the [`Dockerfile`](Dockerfile) and [`railway.json`](railway.json).

### Required environment variables

| Var | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Production key (freshly rotated) |
| `VOYAGE_API_KEY` | Production key (freshly rotated) |
| `SUPABASE_URL` | From Supabase Settings → API |
| `SUPABASE_SERVICE_KEY` | The **service_role** key (freshly rotated) |
| `ADMIN_TOKEN` | Freshly generated random string |
| `BOOTSTRAP_TOKEN` | Freshly generated random string |
| `ENCRYPTION_KEY` | 64 hex chars for AES-256-GCM (connected_sources.config encryption) |
| `ORG_ID` | `00000000-0000-0000-0000-000000000001` for single-tenant |
| `FRONTEND_URL` | Your Vercel deployment URL — added to CORS allow-list |
| `APP_VERSION` | Whatever your CI sets — surfaced by `GET /health` |

### Optional environment variables

| Var | Default | Purpose |
|---|---|---|
| `INGEST_SCHEDULE_HOURS` | `24` | Scheduler cadence |
| `STALE_THRESHOLD_DAYS` | `90` | Staleness flagging window |
| `ANTHROPIC_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `LOG_LEVEL` | `INFO` | Logger min level |
| `INGEST_REQUIRE_LOCK` | `false` | Set `true` to abort ingest on lock RPC failure |
| `FLOWITHM_URL` | `http://localhost:3000` | Public dashboard host (Slack bot deeplinks) |
| `FLOWITHM_API_URL` | `http://localhost:8000` | Public API host (Slack bot) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_APP_TOKEN` | — | Only if deploying the Slack bot |

### Deploy

1. Create a new Railway project → New Service → Deploy from GitHub repo.
2. Settings → set **Root Directory** to `company-brain`.
3. Settings → Variables → paste the env vars above.
4. Railway auto-builds via the Dockerfile and starts the service.
5. The healthcheck at `/health` is a fast liveness probe (status + version)
   so Railway's 30s healthcheck window is comfortable. The real dependency
   probe (Supabase + Anthropic + Voyage + scheduler) lives at `/health/detailed`.

---

## 3. Vercel — Next.js dashboard

The dashboard lives at `company-brain/ui/`. When creating the Vercel
project, set **Root Directory** to `company-brain/ui`.

### Required environment variables (Settings → Environment Variables)

| Var | Public? | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | From Supabase Settings → API (Project URL) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | From Supabase Settings → API (anon/public key) |
| `SUPABASE_URL` | No | Same URL, server-only |
| `SUPABASE_SERVICE_KEY` | No | Service-role key (server-only) |
| `ADMIN_TOKEN` | No | Same string as backend — used for admin proxy headers + HMAC signing |
| `FLOWITHM_API_URL` | No | Your Railway public URL, e.g. `https://flowithm.up.railway.app` |
| `BOOTSTRAP_TOKEN` | No | Same as backend — used by the signup flow to create orgs |
| `FLOWITHM_PLAYGROUND_KEY` | No | Minted after deploy (see step 4) |

### Optional

| Var | Purpose |
|---|---|
| `FLOWITHM_DEFAULT_ORG_ID` | Bypasses the signup redirect for single-tenant deploys |

### Deploy

1. Vercel Dashboard → New Project → import your GitHub repo.
2. Set **Root Directory** to `company-brain/ui`.
3. Add the env vars above (Production scope).
4. Vercel detects Next.js automatically.

---

## 4. Mint a production playground key

Once Railway is up:

```bash
ADMIN_TOKEN="<your production ADMIN_TOKEN>"
curl -X POST https://your-railway-url.up.railway.app/api/v1/keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "content-type: application/json" \
  -d '{"name":"Playground"}'
```

Copy the `key` from the response, paste into Vercel's
`FLOWITHM_PLAYGROUND_KEY` env, redeploy.

---

## 5. Post-deploy smoke test

```bash
# Liveness probe — should return {"status":"ok","version":"…"}
curl https://your-railway-url.up.railway.app/health

# Full dependency probe — Supabase + Anthropic + Voyage + scheduler all OK
curl https://your-railway-url.up.railway.app/health/detailed

# Visit the Vercel URL → should redirect to /signup
# Create an account → should land on /brain
# Generate a workflow from the home page
# Check /brain → workflow appears
```

---

## 6. Operating notes

- **Workers**: the Dockerfile starts uvicorn with `--workers 1`. The
  threadpool is bumped to 200 in the lifespan. The in-memory rate
  limiter is per-process; bumping workers multiplies the effective
  rate limit. Move to Redis before scaling horizontally.
- **Scheduler mutex**: Railway can only run one instance per service for
  the scheduler to behave correctly. If you deploy multiple replicas,
  the row-mutex (`ingest_lock`) handles the race.
- **Logs**: every line is JSON. Filter on `org_id`, `endpoint`,
  `duration_ms`.
- **Schema migrations**: re-run `brain/schema.sql` end-to-end whenever
  you pull new code. Every statement is idempotent.
- **Auth flow**: Users sign up at `/signup` (Supabase Auth email/password),
  which creates the auth user + organisation + `users` row. Login at
  `/login`. All `/brain/*` routes require an authenticated session.
