// Live API playground — proxies GET /api/v1/skills/match using a single
// server-side $FLOWITHM_PLAYGROUND_KEY so the dashboard never embeds a real
// customer key in the browser. Mint the playground key once via the New key
// flow with name="Playground" and paste the plaintext into .env.local.
//
// Multi-tenancy note: the playground key carries its OWN org_id (set when
// the key was minted), so /skills/match returns matches from THAT tenant —
// not necessarily the dashboard's current tenant. Acceptable for the
// self-hosted single-org default; a multi-tenant SaaS deployment would
// need a per-org playground key (or skip the playground entirely).
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const PLAYGROUND_KEY = process.env.FLOWITHM_PLAYGROUND_KEY || "";

export async function GET(request: Request) {
  if (!PLAYGROUND_KEY) {
    return NextResponse.json(
      {
        error:
          "FLOWITHM_PLAYGROUND_KEY is not set. Mint a key from the dashboard with name='Playground' and add it to ui/.env.local.",
        code: "INTERNAL_ERROR",
        docs: "https://flowithm.io/docs",
      },
      { status: 500 },
    );
  }

  const { searchParams } = new URL(request.url);
  const q = (searchParams.get("q") || "").trim();
  if (!q) {
    return NextResponse.json(
      { error: "Missing ?q parameter.", code: "INVALID_REQUEST", docs: "https://flowithm.io/docs" },
      { status: 400 },
    );
  }

  const started = performance.now();
  try {
    const res = await fetch(
      `${API_URL}/api/v1/skills/match?q=${encodeURIComponent(q)}`,
      {
        headers: { Authorization: `Bearer ${PLAYGROUND_KEY}` },
        cache: "no-store",
      },
    );
    const body = await res.text();
    const elapsed = Math.round(performance.now() - started);
    return new NextResponse(body, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") || "application/json",
        "x-flowithm-elapsed-ms": String(elapsed),
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
