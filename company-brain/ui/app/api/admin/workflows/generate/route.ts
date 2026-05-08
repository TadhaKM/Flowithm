// Server-only proxy: POST /api/admin/workflows/generate -> FastAPI POST /workflows/generate.
// Forwards X-Org-ID from the cookie so the new workflow lands under the
// caller's tenant. Used by the onboarding /generate page and any future
// in-dashboard generator that needs the org binding.
import { NextResponse } from "next/server";

import { MissingOrgSession, orgHeaders, unauthorisedResponse } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: Request) {
  const payload = await request.json().catch(() => ({}));
  try {
    const headers = await orgHeaders({ admin: true, json: true });
    const res = await fetch(`${API_URL}/workflows/generate`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    if (err instanceof MissingOrgSession) return unauthorisedResponse();
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
