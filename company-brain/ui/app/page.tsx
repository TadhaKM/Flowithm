// Root route: the public marketing landing page, for every visitor.
// Signed-in users see dashboard links in place of Sign in / Get started;
// the workflow generator lives at /generate.
import { createClient } from "@/lib/supabase-server";
import { LandingPage } from "@/components/LandingPage";

export const dynamic = "force-dynamic";

export default async function Home() {
  // The landing page must render even when Supabase env/config is missing
  // or the auth server is unreachable — fail soft to signed-out.
  let user = null;
  try {
    const supabase = await createClient();
    ({
      data: { user },
    } = await supabase.auth.getUser());
  } catch {
    user = null;
  }

  return <LandingPage signedIn={!!user} />;
}
