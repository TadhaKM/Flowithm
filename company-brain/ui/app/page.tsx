// Root route: public marketing landing page for signed-out visitors,
// the workflow generator app for authenticated users.
import { createClient } from "@/lib/supabase-server";
import { GeneratorApp } from "@/components/GeneratorApp";
import { LandingPage } from "@/components/LandingPage";

export const dynamic = "force-dynamic";

export default async function Home() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return user ? <GeneratorApp /> : <LandingPage />;
}
