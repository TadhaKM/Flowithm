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
import { isMockBackend } from "./backend";
import { createOrganisation, ensureSeeded } from "./mock/seed";
import { createClient as createAuthClient } from "./supabase-server";
import { getSupabase } from "./supabase";

const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
// Independent secret for the X-Admin-Sig HMAC so a leaked ADMIN_TOKEN can't
// forge org-binding signatures. Falls back to ADMIN_TOKEN when unset to keep
// single-secret deploys working during migration. Must match the FastAPI
// verifier's _admin_signing_key() resolution and the Slack bot's signer.
const ADMIN_SIGNING_KEY = process.env.ADMIN_SIGNING_KEY || ADMIN_TOKEN;
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

  const mock = isMockBackend();
  let orgId: string = user.app_metadata?.org_id || "";

  if (!orgId) {
    // Slow path: first request after signup before JWT refreshes — fall
    // back to a direct users-table lookup via the service-role client.
    const svc = getSupabase();
    const { data } = await svc
      .from("users")
      .select("org_id")
      .eq("id", user.id)
      .maybeSingle();
    orgId = data?.org_id || "";
  }

  // Mock mode: a signed-in user whose signup never got as far as creating
  // an organisation gets one now, so they land on a working dashboard.
  if (!orgId && mock) {
    orgId = await provisionMockOrg(user.id, user.email || "");
  }

  if (orgId && mock) await ensureSeeded(orgId);
  return orgId;
}


const provisioning = new Map<string, Promise<string>>();

function provisionMockOrg(userId: string, email: string): Promise<string> {
  let p = provisioning.get(userId);
  if (!p) {
    p = (async () => {
      const svc = getSupabase();
      const local = email.split("@")[0] || "My";
      const org = await createOrganisation(`${local}'s workspace`);
      const { error } = await svc.from("users").insert({
        id: userId,
        org_id: org.id,
        display_name: "",
        email,
      });
      if (error) {
        // Lost a race with another request (or the signup route): use the
        // org that won and drop the one we just made.
        await svc.from("organisations").delete().eq("id", org.id);
        const { data } = await svc.from("users").select("org_id").eq("id", userId).maybeSingle();
        return data?.org_id || "";
      }
      await svc.auth.admin.updateUserById(userId, { app_metadata: { org_id: org.id } });
      return org.id;
    })().finally(() => provisioning.delete(userId));
    provisioning.set(userId, p);
  }
  return p;
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
      .createHmac("sha256", ADMIN_SIGNING_KEY)
      .update(`${orgForSig}:${ts}`)
      .digest("hex");
    headers["X-Admin-Sig"] = `${ts}:${sig}`;
  }
  if (opts?.json) headers["content-type"] = "application/json";
  return headers;
}


export function adminTokenMissing(): boolean {
  // The in-process mock backend doesn't check the admin token.
  return !ADMIN_TOKEN && !isMockBackend();
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
