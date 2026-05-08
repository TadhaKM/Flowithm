// Shared helper for server-only proxy routes: read the flowithm_org_id
// cookie + the FLOWITHM_DEFAULT_ORG_ID env fallback, return the headers
// every proxy needs (auth + org).
//
// SECURITY: with `admin: true`, this helper REFUSES to fall through to
// the env default when no cookie is present — admin proxies must have a
// real session-bound org id. Without this guard, anyone who hit the
// dashboard origin could mint API keys for the default org via the
// admin endpoints. Public-read paths (GET endpoints already gated on
// the FastAPI side) can keep using the env fallback by not passing
// admin:true.
//
// Usage in a route handler:
//   const headers = await orgHeaders({ admin: true });
//   const res = await fetch(API + "/sources", { headers, ... });
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
const FALLBACK_ORG_ID = process.env.FLOWITHM_DEFAULT_ORG_ID || "";


export class MissingOrgSession extends Error {
  constructor() {
    super("No org session — set the flowithm_org_id cookie via /setup first.");
  }
}


export async function getOrgIdFromCookie(): Promise<string> {
  const c = await cookies();
  return c.get("flowithm_org_id")?.value || "";
}


export async function getOrgId(): Promise<string> {
  const fromCookie = await getOrgIdFromCookie();
  return fromCookie || FALLBACK_ORG_ID;
}


export async function orgHeaders(opts?: { admin?: boolean; json?: boolean }): Promise<HeadersInit> {
  const headers: Record<string, string> = {};
  if (opts?.admin) {
    // Admin path requires a real cookie-bound session. We refuse the env
    // fallback because every admin proxy hits an endpoint that mutates
    // tenant data, and falling back to FLOWITHM_DEFAULT_ORG_ID would
    // let any unauthenticated visitor target the default org.
    const cookieOrg = await getOrgIdFromCookie();
    if (!cookieOrg) throw new MissingOrgSession();
    headers["X-Org-ID"] = cookieOrg;
  } else {
    const org = await getOrgId();
    if (org) headers["X-Org-ID"] = org;
  }
  if (opts?.admin && ADMIN_TOKEN) headers["Authorization"] = `Bearer ${ADMIN_TOKEN}`;
  if (opts?.json) headers["content-type"] = "application/json";
  return headers;
}


export function adminTokenMissing(): boolean {
  return !ADMIN_TOKEN;
}


export function unauthorisedResponse(): NextResponse {
  return NextResponse.json(
    {
      error: "Not signed in — set up your organisation via /setup first.",
      code: "MISSING_API_KEY",
      docs: "https://flowithm.io/docs",
    },
    { status: 401 },
  );
}


/**
 * Wrap an admin proxy fetch so MissingOrgSession turns into 401 instead
 * of 502, and any other failure reports the original error. Use:
 *
 *     return adminFetch(async () => {
 *       const headers = await orgHeaders({ admin: true });
 *       const res = await fetch(...);
 *       const body = await res.text();
 *       return new NextResponse(body, { status: res.status, headers: ... });
 *     });
 */
export async function adminFetch(
  fn: () => Promise<NextResponse>,
): Promise<NextResponse> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof MissingOrgSession) return unauthorisedResponse();
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
