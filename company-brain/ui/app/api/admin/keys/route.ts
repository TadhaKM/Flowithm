// Server-only proxy for /api/v1/keys (list + create). Injects ADMIN_TOKEN
// AND X-Org-ID so the agent API scopes the key list / new key to the
// dashboard's tenant.
import { NextResponse } from "next/server";

import { adminTokenMissing, getOrgId, orgHeaders } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

function notConfigured() {
  return NextResponse.json(
    {
      error: "ADMIN_TOKEN is not set in the dashboard's environment.",
      code: "INTERNAL_ERROR",
      docs: "https://flowithm.io/docs",
    },
    { status: 500 },
  );
}

export async function GET() {
  if (adminTokenMissing()) return notConfigured();
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await fetch(`${API_URL}/api/v1/keys`, { headers, cache: "no-store" });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}

export async function POST(request: Request) {
  if (adminTokenMissing()) return notConfigured();
  const payload = await request.json().catch(() => ({}));
  // Inject org_id into the body so the new key gets bound to the dashboard
  // tenant. Caller can override by including org_id in their own payload
  // (admin users picking which tenant to mint for).
  const orgId = await getOrgId();
  if (orgId && !payload.org_id) payload.org_id = orgId;
  try {
    const headers = await orgHeaders({ admin: true, json: true });
    const res = await fetch(`${API_URL}/api/v1/keys`, {
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
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
