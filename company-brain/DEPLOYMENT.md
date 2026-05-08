# Deployment checklist

Production target: **Railway** for the FastAPI backend, **Vercel** for
the Next.js dashboard. Supabase Postgres for storage. Anthropic + Voyage
for the model providers.

> [!NOTE]
> The Slack bot (`slack/app.py`) runs Socket Mode — it doesn't expose a
> port, so Railway's web service won't run it. Deploy it as a separate
> Railway service or worker, or run it on your own host.

---

## 1. Supabase — schema + seed

Before the first deploy, open the Supabase SQL editor and run
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
`organisations`, `skills`.

The schema also seeds a default organisation with UUID
`00000000-0000-0000-0000-000000000001`. Self-hosted single-tenant deploys
keep that as the active org via `ORG_ID` env.

---

## 2. Railway — FastAPI backend

The repository's Python project lives at `company-brain/`. When creating
the Railway service, set **rootDirectory** to `company-brain` so it picks
up the [`Dockerfile`](Dockerfile) and [`railway.json`](railway.json).

### Required environment variables

| Var | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Production key from console.anthropic.com |
| `VOYAGE_API_KEY` | Production key from dash.voyageai.com |
| `SUPABASE_URL` | From your Supabase project's Settings → API |
| `SUPABASE_SERVICE_KEY` | The **service_role** key, server-only |
| `ADMIN_TOKEN` | Generate fresh: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Required on every internal endpoint (`/query`, `/skills`, `/workflows/*`, `/history`, `/conflicts`, `/sources`, `/ingest/status`) — the C-4 lockdown made this the canonical org gate. |
| `BOOTSTRAP_TOKEN` | Required to call `POST /setup` after the first organisation has been created. Without it, the public internet could DoS the DB by minting orgs and farm cookies for the dashboard's admin proxies. |
| `FLOWITHM_ACTION_SECRET` | Optional. Signing key for Slack interactive-button payloads (HMAC-SHA-256). If unset, falls back to `ADMIN_TOKEN`. Set independently if you ever rotate `ADMIN_TOKEN` without invalidating in-flight Slack messages. |
| `ORG_ID` | `00000000-0000-0000-0000-000000000001` for single-tenant; the org's UUID for multi-tenant deploys |
| `FRONTEND_URL` | Your Vercel deployment URL — added to CORS allow-list |
| `APP_VERSION` | Whatever your CI sets — surfaced by `GET /health` |

### Optional environment variables

| Var | Default | Purpose |
|---|---|---|
| `INGEST_SCHEDULE_HOURS` | `24` | Scheduler cadence |
| `STALE_THRESHOLD_DAYS` | `90` | Staleness flagging window |
| `ANTHROPIC_TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `LOG_LEVEL` | `INFO` | Logger min level |
| `FLOWITHM_URL` | `http://localhost:3000` | Public dashboard host (used by the Slack bot to build deeplinks) |
| `FLOWITHM_API_URL` | `http://localhost:8000` | Public API host (used by the Slack bot) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_APP_TOKEN` | — | Only if you're deploying the Slack bot |

### Deploy

1. Create a new Railway project → New Service → Deploy from GitHub repo.
2. Settings → set **Root Directory** to `company-brain`.
3. Settings → Variables → paste the env vars above.
4. Railway auto-builds via the Dockerfile and starts the service.
5. The healthcheck at `/health` controls Railway's "deployed" state — it
   exercises Supabase + Anthropic + Voyage + scheduler. A degraded probe
   doesn't fail the deploy by default; check the response body to see
   which dependency is sick.

---

## 3. Vercel — Next.js dashboard

The dashboard lives at `company-brain/ui/`. When creating the Vercel
project, set **Root Directory** to `company-brain/ui`.

### Required environment variables (Settings → Environment Variables)

| Var | Notes |
|---|---|
| `FLOWITHM_API_URL` | Your Railway public URL, e.g. `https://flowithm.railway.app` |
| `NEXT_PUBLIC_API_URL` | Same value — exposed to the browser via `vercel.json` |
| `SUPABASE_URL` | Same as backend |
| `SUPABASE_SERVICE_KEY` | Same as backend; server-only — Next.js only reads this in route handlers, never ships to the client |
| `ADMIN_TOKEN` | Same string as the backend's |
| `FLOWITHM_PLAYGROUND_KEY` | Plaintext of a key minted via `POST /api/v1/keys` with `name="Playground"` against the production deployment |

### Optional

| Var | Purpose |
|---|---|
| `FLOWITHM_DEFAULT_ORG_ID` | Bypasses the `/setup` redirect for single-tenant deploys. Set to `00000000-0000-0000-0000-000000000001` (or whatever org UUID you want all visitors to land on). |

### Deploy

1. Vercel Dashboard → New Project → import your GitHub repo.
2. Set **Root Directory** to `company-brain/ui`.
3. Add the env vars above (Production scope).
4. Vercel detects Next.js automatically; the [`vercel.json`](ui/vercel.json) is supplemental.

> **Why service_role and not anon + RLS?** RLS policies are deferred —
> see the TODO at the top of `brain/schema.sql`. Until they land, the
> Next.js server-only routes use the service_role key and enforce org
> filtering in application code (`brain/store._default_org_id` →
> `lib/org.getOrgId`). Before opening the dashboard to untrusted users,
> add policies that scope every read/write to `current_setting('app.org_id')`
> and switch to anon + JWT.

> **Dashboard auth is a session cookie + admin token, NOT real user
> auth.** The Next.js admin proxies inject `ADMIN_TOKEN` server-side, but
> the only "user identity" is the `flowithm_org_id` httpOnly cookie set
> by `/setup`. Anyone who hits the dashboard origin and POSTs `/setup`
> gets a cookie. Until you wire a real auth provider (Supabase Auth /
> Clerk / NextAuth) **the dashboard MUST be behind a network-layer gate**
> — Vercel Password Protection, Cloudflare Access, or equivalent. The
> backend lockdown (C-4) means a leaked dashboard cookie can't trash
> arbitrary tenants' data without also having `ADMIN_TOKEN`, but a
> dashboard origin you treat as public would still let visitors mint
> orgs (gated by `BOOTSTRAP_TOKEN` after the first one) and farm
> cookies. Treat as single-tenant only until real auth ships.

---

## 4. Mint a production playground key

Once Railway is up:

```powershell
$ADMIN_TOKEN = "<your production ADMIN_TOKEN>"
$body = @{ name = "Playground" } | ConvertTo-Json
$resp = Invoke-WebRequest -UseBasicParsing -Method Post `
  -Uri "https://your-railway-url.railway.app/api/v1/keys" `
  -Headers @{ Authorization = "Bearer $ADMIN_TOKEN"; "content-type" = "application/json" } `
  -Body $body
($resp.Content | ConvertFrom-Json).key
```

Copy the plaintext, paste into Vercel's `FLOWITHM_PLAYGROUND_KEY` env,
redeploy.

---

## 5. Post-deploy smoke test

Run these against your production URLs (PowerShell + `Invoke-WebRequest`,
or curl):

```bash
# Health probe — should return status:"ok" with every check OK
curl https://your-railway-url.railway.app/health

# Mint a customer key
curl -X POST https://your-railway-url.railway.app/api/v1/keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "content-type: application/json" \
  -d '{"name":"Production"}'
# → save the `key` from the response

# List skills (empty until you generate something)
curl -H "Authorization: Bearer $KEY" \
  https://your-railway-url.railway.app/api/v1/skills

# Semantic match
curl -H "Authorization: Bearer $KEY" \
  "https://your-railway-url.railway.app/api/v1/skills/match?q=customer+refund"
```

Then visit the Vercel URL → first load redirects to `/setup` (unless
`FLOWITHM_DEFAULT_ORG_ID` is set) → enter a company name → land on
`/brain`. Connect a source via `/brain/sources` → Sync now → check the
Railway logs for `[Flowithm scheduler] cycle done`.

---

## 6. Operating notes

- **Workers**: the Dockerfile starts uvicorn with `--workers 1`. The
  in-memory rate limiter (`api/auth.py`) is per-process; bumping workers
  multiplies the effective rate limit. Move to Redis before scaling
  horizontally — TODO comment in the Dockerfile.
- **Scheduler mutex**: Railway can only run one instance per service for
  the scheduler to behave correctly without duplicating cycles. If you
  deploy multiple replicas, the row-mutex (`ingest_lock`) handles the
  race — only one instance will run the cycle, the rest skip.
- **Logs**: every line is JSON. Railway → Logs supports filtering on
  structured fields. Look for `org_id`, `endpoint`, `duration_ms` for
  per-request slicing.
- **Schema migrations**: re-run `brain/schema.sql` end-to-end whenever
  you pull new code. Every `ALTER` and `CREATE` is `IF NOT EXISTS`, so
  it's idempotent.
- **Rolling back**: `git revert <hash>` + push. Railway/Vercel will
  rebuild from the new HEAD. Database changes don't roll back
  automatically — keep a Supabase backup before destructive migrations.
