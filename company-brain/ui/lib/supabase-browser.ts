// Browser-side Supabase client for "use client" components (login, signup,
// sign-out). Uses the public anon key — safe to expose to the browser.
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
