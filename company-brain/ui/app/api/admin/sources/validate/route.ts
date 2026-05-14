// Server-only proxy for POST /sources/validate — pre-save connection check
// for the Connect-source modal. Admin-only; injects ADMIN_TOKEN + X-Org-ID.
import { NextResponse } from "next/server";

import { MissingOrgSession, adminTokenMissing, orgHeaders, unauthorisedResponse } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: Request) {
  if (adminTokenMissing()) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  const payload = await request.json().catch(() => ({}));
  try {
    const headers = await orgHeaders({ admin: true, json: true });
    const res = await fetch(`${API_URL}/sources/validate`, {
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
