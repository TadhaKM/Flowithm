// Server-only proxy for PATCH + DELETE on a single source. Both admin-only.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

function adminGuard() {
  if (!ADMIN_TOKEN) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  return null;
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = adminGuard();
  if (guard) return guard;
  const { id } = await params;
  const payload = await request.json().catch(() => ({}));
  try {
    const res = await fetch(`${API_URL}/sources/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${ADMIN_TOKEN}`,
        "content-type": "application/json",
      },
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

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const guard = adminGuard();
  if (guard) return guard;
  const { id } = await params;
  try {
    const res = await fetch(`${API_URL}/sources/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
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
