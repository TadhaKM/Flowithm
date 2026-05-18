// Server-side proxy for the public /demo/{slug} endpoint. The backend
// route has no admin gate (it just serves a static demo-data file) but
// fetching it from the browser would require either a CORS allowlist
// entry for the Vercel origin or NEXT_PUBLIC_API_URL set client-side.
// Going through a Next.js route fixes both: same-origin from the
// browser, server-side FLOWITHM_API_URL lookup.
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  try {
    const res = await fetch(`${API_URL}/demo/${encodeURIComponent(slug)}`, {
      cache: "no-store",
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") || "text/plain" },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
