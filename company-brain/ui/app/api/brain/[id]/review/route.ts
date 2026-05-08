// Server-only proxy: POST /api/brain/{id}/review -> FastAPI POST /skills/{id}/review.
// No body required; clears the staleness flag and bumps reviewed_at.
import { NextResponse } from "next/server";

import { MissingOrgSession, orgHeaders, unauthorisedResponse } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await fetch(`${API_URL}/skills/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers,
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
