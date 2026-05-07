// Shared helper for server-only proxy routes: read the flowithm_org_id
// cookie + the FLOWITHM_DEFAULT_ORG_ID env fallback, return the headers
// every proxy needs (auth + org).
//
// Usage in a route handler:
//   const headers = await orgHeaders({ admin: true });
//   const res = await fetch(API + "/sources", { headers, ... });

import { cookies } from "next/headers";

const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
const FALLBACK_ORG_ID = process.env.FLOWITHM_DEFAULT_ORG_ID || "";

export async function getOrgId(): Promise<string> {
  const c = await cookies();
  const fromCookie = c.get("flowithm_org_id")?.value || "";
  return fromCookie || FALLBACK_ORG_ID;
}

export async function orgHeaders(opts?: { admin?: boolean; json?: boolean }): Promise<HeadersInit> {
  const headers: Record<string, string> = {};
  const org = await getOrgId();
  if (org) headers["X-Org-ID"] = org;
  if (opts?.admin && ADMIN_TOKEN) headers["Authorization"] = `Bearer ${ADMIN_TOKEN}`;
  if (opts?.json) headers["content-type"] = "application/json";
  return headers;
}

export function adminTokenMissing(): boolean {
  return !ADMIN_TOKEN;
}
