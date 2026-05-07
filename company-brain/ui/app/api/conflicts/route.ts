// Proxy /api/conflicts -> FastAPI /conflicts. Server-side so the FastAPI
// host stays out of the client bundle; matches the Slack bot's env var name.
import { NextResponse } from "next/server";

import { orgHeaders } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const includeSnoozed = searchParams.get("include_snoozed") === "true";
  const url = `${API_URL}/conflicts?include_snoozed=${includeSnoozed ? "true" : "false"}`;
  try {
    const headers = await orgHeaders();
    const res = await fetch(url, { headers, cache: "no-store" });
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
