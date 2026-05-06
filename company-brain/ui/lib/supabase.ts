// Server-side Supabase client used by the /app/api/brain/* routes.
// Must NEVER be imported from a "use client" component — the service-role
// key would leak to the browser. The lack of NEXT_PUBLIC_ prefix on the
// env var keeps Next from accidentally serializing it.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set — copy them from " +
        "the project root .env into ui/.env.local (see ui/.env.local.example).",
    );
  }
  _client = createClient(url, key, { auth: { persistSession: false } });
  return _client;
}
