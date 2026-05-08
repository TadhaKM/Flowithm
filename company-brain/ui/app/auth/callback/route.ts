// Supabase email-verification callback.
// After a user clicks the confirmation link in their email, Supabase
// redirects here with ?code=xxx (PKCE flow). We exchange the code for a
// session, create the organisation via FastAPI, link the user in Supabase,
// then redirect to /brain.
//
// Company details travel as ?c=<name>&d=<displayName> — extras that
// Supabase preserves when it appends ?code=xxx to the redirectTo URL.
import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { getSupabase } from "@/lib/supabase";

const API_URL = (process.env.FLOWITHM_API_URL || "http://localhost:8000").replace(/\/$/, "");
const BOOTSTRAP_TOKEN = process.env.BOOTSTRAP_TOKEN || "";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const companyName = (searchParams.get("c") || "").trim();
  const displayName = (searchParams.get("d") || "").trim();

  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=missing_code`);
  }

  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return cookieStore.getAll(); },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        },
      },
    },
  );

  const { error: sessionErr } = await supabase.auth.exchangeCodeForSession(code);
  if (sessionErr) {
    return NextResponse.redirect(`${origin}/login?error=verification_failed`);
  }

  // Get the confirmed user.
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.redirect(`${origin}/login?error=no_user`);
  }

  // If no company name was in the URL (e.g. a password-reset link), skip
  // org creation and go straight to /brain.
  if (!companyName) {
    return NextResponse.redirect(`${origin}/brain`);
  }

  try {
    // Check if user already has an org (idempotency guard).
    const svc = getSupabase();
    const { data: existing } = await svc
      .from("users")
      .select("org_id")
      .eq("id", user.id)
      .limit(1);

    if (existing && existing.length > 0) {
      return NextResponse.redirect(`${origin}/brain`);
    }

    // Create the organisation via FastAPI.
    const setupHeaders: Record<string, string> = { "content-type": "application/json" };
    if (BOOTSTRAP_TOKEN) setupHeaders["Authorization"] = `Bearer ${BOOTSTRAP_TOKEN}`;

    const setupRes = await fetch(`${API_URL}/setup`, {
      method: "POST",
      headers: setupHeaders,
      body: JSON.stringify({ company_name: companyName }),
      cache: "no-store",
    });

    if (!setupRes.ok) {
      // Org creation failed — redirect to /brain; user will see the 401
      // banner and can contact support or retry.
      return NextResponse.redirect(`${origin}/brain`);
    }

    const org = await setupRes.json();
    const orgId = String(org.id);

    // Link the auth user to the new org in the users table.
    await svc.from("users").insert({
      id: user.id,
      org_id: orgId,
      display_name: displayName,
      email: user.email || "",
    });

    // Embed org_id in the JWT app_metadata for fast resolution on future requests.
    await svc.auth.admin.updateUserById(user.id, {
      app_metadata: { org_id: orgId },
    });
  } catch {
    // Non-fatal: user is authenticated, /brain will show the error state.
  }

  return NextResponse.redirect(`${origin}/brain`);
}
