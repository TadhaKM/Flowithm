// Shared helper for server-only proxy routes: resolve the authenticated
// user's organisation from the Supabase Auth session, and build the
// headers every proxy needs (auth + org).
//
// SECURITY: with `admin: true`, this helper REFUSES to fall through to
// the env default when no session is present — admin proxies must have a
// real session-bound org id. Without this guard, anyone who hit the
// dashboard origin could mint API keys for the default org via the admin
// endpoints.
//
// Usage in a route handler:
//   const headers = await orgHeaders({ admin: true });
//   const res = await fetch(API + "/sources", { headers, ... });
import crypto from "crypto";
import { NextResponse } from "next/server";
import { createClient as createAuthClient } from "./supabase-server";
import { getSupabase } from "./supabase";

const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
const FALLBACK_ORG_ID = process.env.FLOWITHM_DEFAULT_ORG_ID || "";


export class MissingOrgSession extends Error {
  constructor() {
    super("No authenticated session — sign in at /login first.");
  }
}


export async function getOrgIdFromSession(): Promise<string> {
  const supabase = await createAuthClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return "";

  // Fast path: org_id cached in the user's app_metadata (set at signup).
  if (user.app_metadata?.org_id) return user.app_metadata.org_id;

  // Slow path: first request after signup before JWT refreshes — fall
  // back to a direct users-table lookup via the service-role client.
  const svc = getSupabase();
  const { data } = await svc
    .from("users")
    .select("org_id")
    .eq("id", user.id)
    .single();

  return data?.org_id || "";
}


export async function getOrgId(): Promise<string> {
  const fromSession = await getOrgIdFromSession();
  return fromSession || FALLBACK_ORG_ID;
}


export async function orgHeaders(opts?: { admin?: boolean; json?: boolean }): Promise<HeadersInit> {
  const headers: Record<string, string> = {};
  if (opts?.admin) {
    // Admin path requires a real session-bound org. We refuse the env
    // fallback because every admin proxy hits an endpoint that mutates
    // tenant data, and falling back to FLOWITHM_DEFAULT_ORG_ID would
    // let any unauthenticated visitor target the default org.
    const sessionOrg = await getOrgIdFromSession();
    if (!sessionOrg) throw new MissingOrgSession();
    headers["X-Org-ID"] = sessionOrg;
  } else {
    const org = await getOrgId();
    if (org) headers["X-Org-ID"] = org;
  }
  if (opts?.admin && ADMIN_TOKEN) {
    headers["Authorization"] = `Bearer ${ADMIN_TOKEN}`;
    // H-NEW-2: HMAC-sign org_id + timestamp so FastAPI can verify the
    // caller is the dashboard proxy (knows the key) and the org_id hasn't
    // been tampered with. Prevents a leaked ADMIN_TOKEN from targeting
    // arbitrary tenants via raw curl.
    const orgForSig = headers["X-Org-ID"] || "";
    const ts = Math.floor(Date.now() / 1000).toString();
    const sig = crypto
      .createHmac("sha256", ADMIN_TOKEN)
      .update(`${orgForSig}:${ts}`)
      .digest("hex");
    headers["X-Admin-Sig"] = `${ts}:${sig}`;
  }
  if (opts?.json) headers["content-type"] = "application/json";
  return headers;
}


export function adminTokenMissing(): boolean {
  return !ADMIN_TOKEN;
}


export function unauthorisedResponse(): NextResponse {
  return NextResponse.json(
    {
      error: "Not signed in.",
      code: "MISSING_SESSION",
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
