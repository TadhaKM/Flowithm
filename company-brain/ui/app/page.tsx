// Root route: public marketing landing page for signed-out visitors,
// the workflow generator app for authenticated users.
import { createClient } from "@/lib/supabase-server";
import { GeneratorApp } from "@/components/GeneratorApp";
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

  return user ? <GeneratorApp /> : <LandingPage />;
}
