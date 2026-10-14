// Workflow generator. Lived at "/" until the landing page took over the
// root for every visitor; signed-in users reach it from the top nav.
// Protected by middleware.ts like every non-public route.
import { GeneratorApp } from "@/components/GeneratorApp";

export default function GeneratePage() {
  return <GeneratorApp />;
}
