// Server-only proxy for /sources (list + create). GET is public on the
// FastAPI side (config is already redacted there), so we don't need
// ADMIN_TOKEN for read. POST is admin-gated → inject the token.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/sources`, { cache: "no-store" });
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
  if (!ADMIN_TOKEN) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  const payload = await request.json().catch(() => ({}));
  try {
    const res = await fetch(`${API_URL}/sources`, {
      method: "POST",
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
