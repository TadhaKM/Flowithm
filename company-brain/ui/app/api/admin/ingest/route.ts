// Server-only proxy for /ingest/status (read) + /ingest/trigger (admin POST).
// Both pass X-Org-ID so /ingest/status returns the dashboard's tenant's
// last run rather than the cross-org aggregate cached in memory.
import { NextResponse } from "next/server";

import { MissingOrgSession, adminTokenMissing, orgHeaders, unauthorisedResponse } from "@/lib/org";
import { backendFetch } from "@/lib/backend";

export async function GET() {
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await backendFetch(`/ingest/status`, { headers, cache: "no-store" });
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

export async function POST() {
  if (adminTokenMissing()) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await backendFetch(`/ingest/trigger`, {
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
