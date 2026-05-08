import { NextResponse } from "next/server";
import { orgHeaders } from "@/lib/org";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");

export async function GET() {
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await fetch(`${API_URL}/history?limit=20`, { headers, cache: "no-store" });
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

export async function DELETE() {
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await fetch(`${API_URL}/history`, { method: "DELETE", headers, cache: "no-store" });
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
