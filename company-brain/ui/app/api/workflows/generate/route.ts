import { NextResponse } from "next/server";
import { orgHeaders } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: Request) {
  try {
    const headers = await orgHeaders({ admin: true, json: true });
    const body = await request.text();
    const res = await fetch(`${API_URL}/workflows/generate`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
    });
    const text = await res.text();
    return new NextResponse(text, {
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
