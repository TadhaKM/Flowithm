// Legacy setup proxy — retained as a thin passthrough to FastAPI /setup
// for backward compatibility. The flowithm_org_id cookie is no longer set
// (Supabase Auth sessions replaced it). Use /api/auth/signup instead.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: Request) {
  const payload = await request.json().catch(() => ({}));
  try {
    const res = await fetch(`${API_URL}/setup`, {
      method: "POST",
      headers: { "content-type": "application/json" },
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
