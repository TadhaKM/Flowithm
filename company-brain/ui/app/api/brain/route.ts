// GET /api/brain — list all non-archived workflows for the knowledge-base
// dashboard. The /brain page filters and sorts client-side; this route is
// just the unfiltered fetch (with an optional source pre-filter for
// linkability — /brain?source=slack works with refresh).
import { NextResponse } from "next/server";

import { getSupabase } from "@/lib/supabase";

const SOURCE_FILTERS = new Set(["slack", "notion", "manual", "github"]);

type SkillRow = {
  id: string;
  process_name: string;
  process_trigger: string | null;
  steps: unknown[] | null;
  decision_rules: string[] | null;
  approvals: string[] | null;
  exceptions: string[] | null;
  sources: string[] | null;
  source: string | null;
  source_metadata: Record<string, unknown> | null;
  generated_at: string | null;
  reviewed_at: string | null;
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const source = url.searchParams.get("source") || "all";

  let query;
  try {
    query = getSupabase()
      .from("skills")
      .select(
        "id, process_name, process_trigger, steps, decision_rules, approvals, exceptions, sources, source, source_metadata, generated_at, reviewed_at",
      )
      .eq("archived", false)
      .order("generated_at", { ascending: false });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "supabase init failed" },
      { status: 500 },
    );
  }

  if (source !== "all" && SOURCE_FILTERS.has(source)) {
    query = query.eq("source", source);
  }

  const { data, error } = await query;
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const workflows = (data ?? []).map((r: SkillRow) => ({
    id: r.id,
    process: r.process_name,
    trigger: r.process_trigger || "",
    steps: r.steps || [],
    decision_rules: r.decision_rules || [],
    approvals: r.approvals || [],
    exceptions: r.exceptions || [],
    sources: r.sources || [],
    source: r.source || "manual",
    source_metadata: r.source_metadata || {},
    generated_at: r.generated_at,
    reviewed_at: r.reviewed_at,
  }));

  return NextResponse.json({ workflows });
}
