// Server-only proxy for DELETE /api/v1/keys/{id} (revoke).
import { NextResponse } from "next/server";

import { MissingOrgSession, adminTokenMissing, orgHeaders, unauthorisedResponse } from "@/lib/org";
import { backendFetch } from "@/lib/backend";

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  if (adminTokenMissing()) {
    return NextResponse.json(
      {
        error: "ADMIN_TOKEN is not set in the dashboard's environment.",
        code: "INTERNAL_ERROR",
        docs: "https://flowithm.io/docs",
      },
      { status: 500 },
    );
  }
  const { id } = await params;
  try {
    const headers = await orgHeaders({ admin: true });
    const res = await backendFetch(`/api/v1/keys/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers,
      cache: "no-store",
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    if (err instanceof MissingOrgSession) return unauthorisedResponse();
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
