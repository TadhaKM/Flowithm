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
