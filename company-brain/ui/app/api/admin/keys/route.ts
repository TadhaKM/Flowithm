// Server-only proxy for /api/v1/keys (list + create). Injects ADMIN_TOKEN
// from process.env so the plaintext never reaches the browser bundle.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

function adminHeaders(): Record<string, string> {
  return {
    Authorization: `Bearer ${ADMIN_TOKEN}`,
    "content-type": "application/json",
  };
}

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
  if (!ADMIN_TOKEN) return notConfigured();
  try {
    const res = await fetch(`${API_URL}/api/v1/keys`, {
      headers: adminHeaders(),
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

export async function POST(request: Request) {
  if (!ADMIN_TOKEN) return notConfigured();
  const payload = await request.json().catch(() => ({}));
  try {
    const res = await fetch(`${API_URL}/api/v1/keys`, {
      method: "POST",
      headers: adminHeaders(),
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
