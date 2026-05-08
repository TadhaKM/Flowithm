// Server-side Supabase client for route handlers and server components.
// Reads/writes session cookies via next/headers. Uses the public anon key
// so RLS policies apply (the service-role client in ./supabase.ts bypasses
// RLS and is used for admin data access).
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // setAll may be called from a Server Component where cookies
            // are read-only. Middleware handles session refresh instead.
          }
        },
      },
    },
  );
}
