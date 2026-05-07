// Server-only: POST /api/setup -> FastAPI POST /setup, then sets the
// `flowithm_org_id` cookie (httpOnly, 1 year). Subsequent admin proxy
// routes read this cookie and forward as the X-Org-ID header.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

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
    if (!res.ok) {
      return new NextResponse(body, {
        status: res.status,
        headers: { "content-type": res.headers.get("content-type") || "application/json" },
      });
    }
    const parsed = JSON.parse(body);
    const orgId = String(parsed?.id || "");
    const out = NextResponse.json(parsed, { status: 200 });
    if (orgId) {
      // httpOnly so the browser can't read or modify it from JS.
      // secure so it never ships over plaintext HTTP (a staging deploy
      // without TLS would leak the cookie otherwise).
      // sameSite=lax prevents cross-site POSTs from re-using it.
      // The proxy routes read it server-side and forward to FastAPI.
      out.cookies.set({
        name: "flowithm_org_id",
        value: orgId,
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        path: "/",
        maxAge: ONE_YEAR_SECONDS,
      });
    }
    return out;
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
