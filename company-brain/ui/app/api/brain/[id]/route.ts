// GET    /api/brain/[id] — full workflow for the detail page (includes raw_text).
// PATCH  /api/brain/[id] — update one of: process_name, reviewed_at, archived.
import { NextResponse } from "next/server";

import { getSupabase } from "@/lib/supabase";

type RouteContext = { params: Promise<{ id: string }> };

type SkillRow = {
  id: string;
  process_name: string;
  description: string | null;
  process_trigger: string | null;
  steps: unknown[] | null;
  decision_rules: string[] | null;
  approvals: string[] | null;
  exceptions: string[] | null;
  sources: string[] | null;
  source: string | null;
  source_metadata: Record<string, unknown> | null;
  raw_text: string | null;
  archived: boolean | null;
  archived_at: string | null;
  reviewed_at: string | null;
  generated_at: string | null;
};

function rowToWorkflow(r: SkillRow) {
  return {
    id: r.id,
    process: r.process_name,
    description: r.description || "",
    trigger: r.process_trigger || "",
    steps: r.steps || [],
    decision_rules: r.decision_rules || [],
    approvals: r.approvals || [],
    exceptions: r.exceptions || [],
    sources: r.sources || [],
    source: r.source || "manual",
    source_metadata: r.source_metadata || {},
    raw_text: r.raw_text || "",
    archived: !!r.archived,
    archived_at: r.archived_at,
    reviewed_at: r.reviewed_at,
    generated_at: r.generated_at,
  };
}

export async function GET(_request: Request, ctx: RouteContext) {
  const { id } = await ctx.params;
  const { data, error } = await getSupabase()
    .from("skills")
    .select("*")
    .eq("id", id)
    .single();
  if (error || !data) {
    return NextResponse.json(
      { error: error?.message || "workflow not found" },
      { status: 404 },
    );
  }
  return NextResponse.json(rowToWorkflow(data as SkillRow));
}

export async function PATCH(request: Request, ctx: RouteContext) {
  const { id } = await ctx.params;
  let body: Record<string, unknown> = {};
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }

  const update: Record<string, unknown> = {};
  if (typeof body.process_name === "string" && body.process_name.trim()) {
    update.process_name = body.process_name.trim();
  }
  if (body.reviewed_at === "now" || body.reviewed_at === true) {
    update.reviewed_at = new Date().toISOString();
  } else if (body.reviewed_at === null) {
    update.reviewed_at = null;
  }
  if (typeof body.archived === "boolean") {
    update.archived = body.archived;
    update.archived_at = body.archived ? new Date().toISOString() : null;
  }

  if (Object.keys(update).length === 0) {
    return NextResponse.json(
      { error: "no valid fields to update (process_name, reviewed_at, archived)" },
      { status: 400 },
    );
  }

  const { data, error } = await getSupabase()
    .from("skills")
    .update(update)
    .eq("id", id)
    .select()
    .single();
  if (error || !data) {
    return NextResponse.json(
      { error: error?.message || "update failed" },
      { status: 500 },
    );
  }
  return NextResponse.json(rowToWorkflow(data as SkillRow));
}
