// Server-only proxy for /ingest/status (read) + /ingest/trigger (admin POST).
import { NextResponse } from "next/server";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/ingest/status`, { cache: "no-store" });
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

export async function POST() {
  if (!ADMIN_TOKEN) {
    return NextResponse.json(
      { error: "ADMIN_TOKEN not set", code: "INTERNAL_ERROR" },
      { status: 500 },
    );
  }
  try {
    const res = await fetch(`${API_URL}/ingest/trigger`, {
      method: "POST",
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
