// Server-only proxy for /api/v1/keys (list + create). Injects ADMIN_TOKEN
// AND X-Org-ID so the agent API scopes the key list / new key to the
// dashboard's tenant.
import { NextResponse } from "next/server";

import { MissingOrgSession, adminTokenMissing, getOrgIdFromSession, orgHeaders } from "@/lib/org";

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

function unauthorised() {
  return NextResponse.json(
    { error: "Not signed in", code: "MISSING_API_KEY", docs: "https://flowithm.io/docs" },
    { status: 401 },
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
    if (err instanceof MissingOrgSession) return unauthorised();
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}

export async function POST(request: Request) {
  if (adminTokenMissing()) return notConfigured();
  const payload = await request.json().catch(() => ({}));
  // C-3 hardening: org_id MUST be the cookie's value. We refuse any
  // user-supplied org_id that doesn't match — closes the trivial
  // "submit {org_id: <victim-uuid>}" cross-tenant key minting path.
  const cookieOrg = await getOrgIdFromSession();
  if (!cookieOrg) return unauthorised();
  if (payload.org_id && payload.org_id !== cookieOrg) {
    return NextResponse.json(
      { error: "org_id mismatch — keys can only be minted for your own tenant.", code: "FORBIDDEN" },
      { status: 403 },
    );
  }
  payload.org_id = cookieOrg;
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
    if (err instanceof MissingOrgSession) return unauthorised();
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
