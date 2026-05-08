// Server-side signup completion. Called by the /signup page after
// Supabase Auth signUp() succeeds client-side. This route:
//   1. Verifies the caller has a valid Supabase session
//   2. Creates the organisation via FastAPI POST /setup
//   3. Inserts a row in the public.users table (auth user → org link)
//   4. Sets org_id in the user's app_metadata for JWT-based resolution
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase-server";
import { getSupabase } from "@/lib/supabase";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const BOOTSTRAP_TOKEN = process.env.BOOTSTRAP_TOKEN || "";

export async function POST(request: Request) {
  // Verify the caller is authenticated.
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  // M-NEW-1: reject if this user already has an org (prevent unbounded
  // org creation from a single authenticated account).
  const svcEarly = getSupabase();
  const { data: existingLink } = await svcEarly
    .from("users")
    .select("id")
    .eq("id", user.id)
    .limit(1);
  if (existingLink && existingLink.length > 0) {
    return NextResponse.json(
      { error: "Account already linked to an organisation" },
      { status: 409 },
    );
  }

  const payload = await request.json().catch(() => ({}));
  const companyName = (payload.company_name || "").trim();
  const displayName = (payload.display_name || "").trim();

  if (!companyName) {
    return NextResponse.json({ error: "company_name is required" }, { status: 400 });
  }

  // Create the organisation via FastAPI.
  const setupHeaders: Record<string, string> = { "content-type": "application/json" };
  if (BOOTSTRAP_TOKEN) {
    setupHeaders["Authorization"] = `Bearer ${BOOTSTRAP_TOKEN}`;
  }

  let org: { id: string; name: string; slug: string };
  try {
    const res = await fetch(`${API_URL}/setup`, {
      method: "POST",
      headers: setupHeaders,
      body: JSON.stringify({ company_name: companyName }),
      cache: "no-store",
    });
    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: text || `Org creation failed (HTTP ${res.status})` },
        { status: res.status },
      );
    }
    org = await res.json();
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }

  const orgId = String(org.id);
  const svc = getSupabase();

  // Link the auth user to the new organisation.
  const { error: insertErr } = await svc.from("users").insert({
    id: user.id,
    org_id: orgId,
    display_name: displayName,
    email: user.email || "",
  });

  if (insertErr) {
    return NextResponse.json({ error: insertErr.message }, { status: 500 });
  }

  // Embed org_id in the JWT's app_metadata so future getUser() calls
  // can resolve the org without an extra DB round-trip.
  await svc.auth.admin.updateUserById(user.id, {
    app_metadata: { org_id: orgId },
  });

  return NextResponse.json({ org_id: orgId, org_name: org.name });
}
