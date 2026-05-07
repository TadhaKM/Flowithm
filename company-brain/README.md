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
