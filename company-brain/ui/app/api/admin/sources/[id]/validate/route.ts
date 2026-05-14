// Server-only proxy for POST /sources/{id}/validate — tests an existing
// source's stored credentials. Admin-only; injects ADMIN_TOKEN + X-Org-ID.
import { NextResponse } from "next/server";

import { MissingOrgSession, adminTokenMissing, orgHeaders, unauthorisedResponse } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  if (adminTokenMissing()) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  const { id } = await params;
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await fetch(`${API_URL}/sources/${encodeURIComponent(id)}/validate`, {
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
