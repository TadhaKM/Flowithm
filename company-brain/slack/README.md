# Flowithm Slack Bot

The bot listens in any channel it's invited to, detects messages that look like internal-process discussions (incident threads, refund questions, runbook chatter), waits 60 seconds for the thread to settle, and offers to extract a structured workflow from it. The extracted workflow lands in the Flowithm knowledge base and can be opened in the UI via a deeplink.

## How it works

```
message in #engineering
       │
       │ matches a trigger pattern + ≥ 20 words?
       ▼
   threading.Timer(60s)
       │
       ▼
   "Flowithm detected a process in this thread"  ──▶  [Extract] [Dismiss]
                                                          │
                                          ┌───────────────┘
                                          ▼
                            ack() → background thread:
                                conversations.replies     ─▶ formatted thread text (≤4K tokens)
                                Claude (process name)     ─▶ 2-5 word title
                                POST /workflows/generate  ─▶ structured JSON + UUID
                                GET  /workflows/similar   ─▶ optional "Update existing" CTA
                                       │
                                       ▼
                               rich Block Kit response with
                               [View full workflow →] [Copy JSON] [Update existing?]
```

Heavy work always happens off the Slack ack-thread (Bolt's `ack()` returns
within Slack's 3-second deadline; the actual extraction runs in a daemon
thread).

## Setup

### 1. Create the Slack app

1. Open https://api.slack.com/apps → **Create New App** → **From scratch**.
2. Name it (e.g. "Flowithm") and pick your workspace.

### 2. OAuth scopes

**OAuth & Permissions** → **Bot Token Scopes**, add:

- `channels:history` — read messages in public channels
- `channels:read` — channel metadata (`conversations.info` → channel name)
- `groups:history` — read messages in private channels
- `groups:read` — private channel metadata
- `chat:write` — post and update messages
- `chat:write.customize` — set username / icon when posting
- `reactions:write` — optional, for future reaction-based UX
- `files:write` — for posting JSON snippets
- `users:read` — resolve author names in collected threads

### 3. Event subscriptions

**Event Subscriptions** → toggle on. Under **Subscribe to bot events**:

- `message.channels`
- `message.groups`
- `app_mention`

If you're using Socket Mode (the default for local dev — see step 4), no Request URL is required.

### 4. Socket Mode

**Socket Mode** → toggle on. Click **Generate an app-level token**, give it `connections:write` scope, save it. This is your `SLACK_APP_TOKEN` (`xapp-…`).

### 5. Interactivity

**Interactivity & Shortcuts** → toggle on. Socket Mode covers the transport — no Request URL needed.

### 6. Install to workspace

**Install App** → **Install to Workspace** → authorize. Copy the **Bot User OAuth Token** (`xoxb-…`); that's your `SLACK_BOT_TOKEN`.

Also grab your **Signing Secret** from **Basic Information** → `SLACK_SIGNING_SECRET`. (Not strictly required by Socket Mode, but worth setting now for any future HTTP-mode deployment.)

### 7. Apply database migrations

The Slack bot writes additional fields to the `skills` table (`source`, `source_metadata`, `archived`, `archived_at`) and uses `pg_trgm` for fuzzy lookups. Re-run [`brain/schema.sql`](../brain/schema.sql) in the Supabase SQL editor — every migration in there is idempotent and safe to re-apply.

### 8. Environment variables

Set these in `.env` at the repo root (see [`.env.example`](../.env.example)):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-...
FLOWBRAIN_URL=http://localhost:3000      # where the UI lives — used in deeplinks
FLOWBRAIN_API_URL=http://localhost:8000  # where the bot POSTs /workflows/generate
```

In production both `FLOWBRAIN_URL` and `FLOWBRAIN_API_URL` typically point to the same host (different ports or paths).

### 9. Install dependencies

```bash
pip install -r slack/requirements.txt
```

The Anthropic + Supabase + python-dotenv deps are already in the root `requirements.txt`; the only new addition is `slack-bolt`.

### 10. Run

In one terminal, the API:

```bash
uvicorn api.main:app --reload
```

In another, the bot:

```bash
python slack/app.py
```

Then in Slack:

```
/invite @Flowithm
```

Drop a 20+ word message that mentions an incident, runbook, or refund policy. After ~60 seconds the bot replies in-thread with the **Extract workflow** button.

## ngrok / HTTP mode (optional)

Socket Mode is the recommended path for local dev. If you'd rather use HTTP (e.g. for staging where Socket Mode isn't desirable):

```bash
ngrok http 3000
```

Paste the ngrok HTTPS URL into Slack:

- **Event Subscriptions** → **Request URL**: `https://xyz.ngrok.io/slack/events`
- **Interactivity & Shortcuts** → **Request URL**: same as above

Then swap `SocketModeHandler` in [`app.py`](app.py) for the `slack_bolt.adapter.fastapi.SlackRequestHandler` (or Flask equivalent), and mount it onto your existing FastAPI app at `/slack/events`. Drop `SLACK_APP_TOKEN` from your env in this mode.

## Trigger patterns

The bot triggers on case-insensitive substring matches against:

- `runbook`, `run book`
- `how do we`, `how does`, `how should we`
- `process for`, `process when`
- `policy for`, `policy on`, `policy when`
- `when a customer`, `when the customer`
- `on-call`, `oncall`, `on call`
- `incident`, `outage`, `postmortem`, `post-mortem`
- `escalation`, `escalate`
- `what happens when`, `what do we do when`
- `SOP` (word-boundary matched), `standard operating procedure`

It does **not** trigger on:

- Bot messages (anything with `bot_id`, including itself)
- DMs and group DMs
- Messages shorter than 20 words
- Edits or deletions

## Troubleshooting

- **Bot doesn't respond at all.** Check OAuth scopes (especially `channels:history`) and confirm event subscriptions include `message.channels` and `message.groups`. Make sure the bot has been invited to the channel.
- **No "Flowithm detected a process" message after typing.** The 60-second wait is intentional. Wait at least 70s before assuming something's broken. Watch the bot's stdout — trigger detection fires immediately and prints when it schedules the timer.
- **"Couldn't extract a workflow" error.** Either the thread is genuinely too thin or Claude refused. The console will show the underlying exception.
- **"Couldn't save to knowledge base" warning at the bottom of the rich response.** The Supabase migration probably wasn't applied — re-run `brain/schema.sql` so the `source` / `source_metadata` columns exist.
- **`module 'slack' has no attribute 'handlers'` on launch.** You're running Python with the deprecated `slack` package shadowing this directory. Uninstall: `pip uninstall slack`. The current SDK is `slack-sdk` (different namespace).

## Files

| File | Purpose |
|---|---|
| [`app.py`](app.py) | Bot entry point — Socket Mode handler, env validation |
| [`handlers.py`](handlers.py) | Message + button handlers, thread collection, Claude calls |
| [`formatter.py`](formatter.py) | Slack Block Kit builders (single source of truth for message shape) |
| [`requirements.txt`](requirements.txt) | Python deps |
| `__init__.py` | Empty — marks `slack` as a package |
