// In-process mock of the FastAPI backend. lib/backend.ts routes every
// proxy call here when mock mode is on, so the dashboard works with only
// Supabase behind it: no FastAPI, Claude or Voyage. State lives in the
// same Supabase tables the real backend uses, scoped by the X-Org-ID the
// proxy routes inject, so data persists across serverless invocations and
// stays isolated per tenant.
//
// Not exposed as an HTTP route: it trusts X-Org-ID, which is only safe
// because the caller is our own server code that resolved the org from
// the Supabase session.
import bcrypt from "bcryptjs";

import { getSupabase } from "@/lib/supabase";

import { DEMO_CONTENT } from "./demo-content";
import { extractWorkflow, jaccard, keywordSimilarity } from "./extract";
import { DISCOVERABLE_CONFLICTS, createOrganisation, ensureSeeded, insertConflict, seedSkillKeyFor } from "./seed";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;

const DOCS_URL = "https://flowithm.io/docs";
const SNOOZE_DAYS = 7;
const SCHEDULE_HOURS = 24;
const SOURCE_TYPES = new Set(["slack", "notion", "github", "gmail", "intercom"]);
const REQUIRED_CONFIG_KEYS: Record<string, string[]> = {
  slack: ["bot_token", "channel_ids"],
  notion: ["integration_token", "page_ids"],
  gmail: ["credentials_json", "label_filters"],
  intercom: ["access_token"],
};
const SECRET_CONFIG_KEYS = new Set([
  "bot_token", "integration_token", "token", "api_key", "secret", "access_token", "credentials_json",
]);

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function codeFor(status: number): string {
  return (
    { 400: "INVALID_REQUEST", 401: "MISSING_API_KEY", 403: "FORBIDDEN", 404: "SKILL_NOT_FOUND", 409: "CONFLICT" } as Record<number, string>
  )[status] || "INTERNAL_ERROR";
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function nowIso(): string {
  return new Date().toISOString();
}

function must<T>(result: { data: T | null; error: { message: string } | null }): T {
  if (result.error) throw new HttpError(500, result.error.message);
  return result.data as T;
}

function rowToWorkflow(r: Row) {
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
    archived_at: r.archived_at ?? null,
    reviewed_at: r.reviewed_at ?? null,
    generated_at: r.generated_at ?? null,
    needs_review: !!r.needs_review,
    needs_review_reason: r.needs_review_reason ?? null,
    version: Number(r.version ?? 1),
  };
}

function rowToAgentSkill(r: Row) {
  return {
    id: String(r.id),
    process: r.process_name || "",
    description: r.description || "",
    trigger: r.process_trigger || "",
    steps: r.steps || [],
    decision_rules: r.decision_rules || [],
    approvals: r.approvals || [],
    exceptions: r.exceptions || [],
    sources: r.sources || [],
    source: r.source || "manual",
    version: Number(r.version ?? 1),
    last_updated: r.generated_at ?? null,
    needs_review: !!r.needs_review,
    needs_review_reason: r.needs_review_reason ?? null,
  };
}

function redactConfig(config: Row | null | undefined): Row {
  const out: Row = {};
  for (const [k, v] of Object.entries(config || {})) out[k] = SECRET_CONFIG_KEYS.has(k) && v ? "***" : v;
  return out;
}

function missingConfig(sourceType: string, config: Row): string[] {
  return (REQUIRED_CONFIG_KEYS[sourceType] || []).filter((k) => {
    const v = config?.[k];
    return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
  });
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

async function activeSkills(orgId: string): Promise<Row[]> {
  return must(
    await getSupabase().from("skills").select("*").eq("org_id", orgId).eq("archived", false).order("generated_at", { ascending: false }),
  );
}

async function generateWorkflow(orgId: string, body: Row) {
  const name = String(body.name || "").trim();
  const content = String(body.content || "");
  if (!name) throw new HttpError(422, "name is required");
  if (!content.trim()) throw new HttpError(422, "content is required");

  const wf = extractWorkflow(name, content);
  const supa = getSupabase();
  const existing = await activeSkills(orgId);

  // Same name as an active workflow → store as its next version (the real
  // backend's unique-active-name index would otherwise reject the insert).
  const prior = existing.find((s) => String(s.process_name).toLowerCase() === wf.process.toLowerCase());
  if (prior) {
    must(await supa.from("skills").update({ archived: true, archived_at: nowIso() }).eq("id", prior.id).eq("org_id", orgId).select("id"));
  }

  const inserted: Row = must(
    await supa
      .from("skills")
      .insert({
        org_id: orgId,
        process_name: wf.process,
        description: wf.description,
        process_trigger: wf.trigger,
        steps: wf.steps,
        decision_rules: wf.decision_rules,
        approvals: wf.approvals,
        exceptions: wf.exceptions,
        sources: wf.sources,
        source: body.source || "manual",
        source_metadata: body.source_metadata || {},
        raw_text: content,
        version: prior ? Number(prior.version ?? 1) + 1 : 1,
        previous_version_id: prior ? prior.id : null,
      })
      .select("*")
      .single(),
  );

  // Mock drift check: flag a heavily overlapping (but differently named)
  // workflow so the conflicts UI has something to react to.
  for (const s of existing) {
    if (prior && s.id === prior.id) continue;
    const overlap = jaccard(`${s.process_name} ${s.process_trigger}`, `${wf.process} ${wf.trigger}`);
    if (overlap < 0.3) continue;
    await insertConflict(orgId, String(s.id), s.process_name, {
      skill: "",
      conflict_type: "update",
      severity: "medium",
      conflict_description: `New source material for "${wf.process}" overlaps with "${s.process_name}" — check whether they describe the same process.`,
      existing_rule: (s.decision_rules || [])[0] || s.process_trigger || s.description || s.process_name,
      new_evidence: wf.decision_rules[0] || wf.trigger,
      suggested_update: wf.decision_rules[0] || wf.description,
      days_ago: 0,
    });
    break;
  }

  return { ...rowToWorkflow(inserted), conflicts: [] };
}

async function getWorkflow(orgId: string, id: string) {
  const rows: Row[] = must(await getSupabase().from("skills").select("*").eq("id", id).eq("org_id", orgId).limit(1));
  if (!rows.length) throw new HttpError(404, `workflow not found: ${id}`);
  return rowToWorkflow(rows[0]);
}

async function history(orgId: string, limit: number) {
  const rows = await activeSkills(orgId);
  return rows.slice(0, limit).map(rowToWorkflow);
}

async function clearHistory(orgId: string) {
  const rows: Row[] = must(await getSupabase().from("skills").delete().eq("org_id", orgId).select("id"));
  return { status: "ok", cleared: rows.length };
}

async function markReviewed(orgId: string, id: string) {
  const rows: Row[] = must(
    await getSupabase()
      .from("skills")
      .update({ reviewed_at: nowIso(), needs_review: false, needs_review_reason: null, stale_flagged_at: null })
      .eq("id", id)
      .eq("org_id", orgId)
      .select("*"),
  );
  if (!rows.length) throw new HttpError(404, `skill not found: ${id}`);
  return rows[0];
}

// ---------------------------------------------------------------------------
// Conflicts
// ---------------------------------------------------------------------------

async function hydrateConflicts(orgId: string, rows: Row[]): Promise<Row[]> {
  const ids = [...new Set(rows.map((r) => r.existing_skill_id).filter(Boolean))];
  const meta = new Map<string, Row>();
  if (ids.length) {
    const skills: Row[] = must(
      await getSupabase().from("skills").select("id,process_name,process_trigger,archived").in("id", ids).eq("org_id", orgId),
    );
    for (const s of skills) meta.set(String(s.id), s);
  }
  for (const r of rows) {
    const m = meta.get(String(r.existing_skill_id || ""));
    r.existing_process_trigger = m?.process_trigger || "";
    if (m?.process_name) r.existing_process_name = m.process_name;
    r.targets_archived_version = m ? !!m.archived : true;
  }
  return rows;
}

async function listConflicts(orgId: string, includeSnoozed: boolean, limit: number) {
  const rows: Row[] = must(
    await getSupabase()
      .from("conflicts")
      .select("*")
      .eq("org_id", orgId)
      .in("status", ["unresolved", "snoozed"])
      .order("created_at", { ascending: false })
      .limit(200),
  );
  const now = Date.now();
  const visible = rows.filter(
    (r) => r.status === "unresolved" || (r.status === "snoozed" && (includeSnoozed || Date.parse(r.snoozed_until || "") < now)),
  );
  const rank: Record<string, number> = { high: 0, medium: 1, low: 2 };
  visible.sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3) || Date.parse(b.created_at) - Date.parse(a.created_at));
  return hydrateConflicts(orgId, visible.slice(0, limit));
}

async function skillConflicts(orgId: string, skillId: string, limit: number) {
  const rows: Row[] = must(
    await getSupabase()
      .from("conflicts")
      .select("*")
      .eq("org_id", orgId)
      .eq("existing_skill_id", skillId)
      .order("created_at", { ascending: false })
      .limit(limit),
  );
  return hydrateConflicts(orgId, rows);
}

/** Apply a suggested update without an LLM: replace the matching rule /
 *  step text verbatim when we can find it, otherwise append it as a rule. */
function applySuggestedUpdate(skill: Row, existingRule: string, update: string) {
  const norm = (s: string) => s.trim().replace(/[.\s]+$/, "").toLowerCase();
  const target = norm(existingRule);
  let replaced = false;
  const swap = (s: string) => {
    if (!replaced && norm(s) === target) {
      replaced = true;
      return update;
    }
    return s;
  };
  const decision_rules = (skill.decision_rules || []).map(swap);
  const approvals = (skill.approvals || []).map(swap);
  const exceptions = (skill.exceptions || []).map(swap);
  const steps = (skill.steps || []).map((st: Row) => ({ ...st, action: swap(String(st.action || "")) }));
  if (!replaced) decision_rules.push(update);
  return { decision_rules, approvals, exceptions, steps };
}

async function resolveConflict(orgId: string, conflictId: string, body: Row) {
  const action = String(body.action || "");
  const resolvedBy = String(body.resolved_by || "dashboard").slice(0, 200);
  if (!["accept", "dismiss", "snooze"].includes(action)) throw new HttpError(400, `unknown action: '${action}'`);

  const supa = getSupabase();
  const found: Row[] = must(await supa.from("conflicts").select("*").eq("id", conflictId).eq("org_id", orgId).limit(1));
  if (!found.length) throw new HttpError(404, `conflict not found: ${conflictId}`);
  const conflict = found[0];

  if (action === "dismiss" || action === "snooze") {
    const patch: Row =
      action === "dismiss"
        ? { status: "dismissed", resolved_by: resolvedBy, resolved_at: nowIso() }
        : {
            status: "snoozed",
            snoozed_until: new Date(Date.now() + SNOOZE_DAYS * 86_400_000).toISOString(),
            resolved_by: resolvedBy,
            resolved_at: nowIso(),
          };
    const rows: Row[] = must(await supa.from("conflicts").update(patch).eq("id", conflictId).eq("org_id", orgId).select("*"));
    return rows[0];
  }

  const skills: Row[] = must(
    await supa.from("skills").select("*").eq("id", conflict.existing_skill_id).eq("org_id", orgId).limit(1),
  );
  if (!skills.length) throw new HttpError(404, `existing skill not found: ${conflict.existing_skill_id}`);
  const old = skills[0];
  if (old.archived) {
    throw new HttpError(
      400,
      "cannot accept a conflict that targets an archived version of the skill — dismiss it and re-run drift detection if still relevant",
    );
  }

  const updated = applySuggestedUpdate(old, conflict.existing_rule || "", conflict.suggested_update || "");
  const newVersion = Number(old.version ?? 1) + 1;

  // Archive first so the unique active-name index doesn't reject the insert.
  must(await supa.from("skills").update({ archived: true, archived_at: nowIso() }).eq("id", old.id).eq("org_id", orgId).select("id"));
  const inserted: Row = must(
    await supa
      .from("skills")
      .insert({
        org_id: orgId,
        process_name: old.process_name,
        description: old.description,
        process_trigger: old.process_trigger,
        steps: updated.steps,
        decision_rules: updated.decision_rules,
        approvals: updated.approvals,
        exceptions: updated.exceptions,
        sources: old.sources,
        source: old.source,
        source_metadata: old.source_metadata,
        raw_text: old.raw_text,
        version: newVersion,
        previous_version_id: old.id,
        reviewed_at: nowIso(),
      })
      .select("*")
      .single(),
  );
  must(
    await supa
      .from("conflicts")
      .update({ status: "accepted", resolved_by: resolvedBy, resolved_at: nowIso(), new_skill_id: inserted.id })
      .eq("id", conflictId)
      .eq("org_id", orgId)
      .select("id"),
  );
  // Cascade: other open conflicts on the old version now point at the new one.
  must(
    await supa
      .from("conflicts")
      .update({ existing_skill_id: inserted.id })
      .eq("org_id", orgId)
      .eq("existing_skill_id", old.id)
      .in("status", ["unresolved", "snoozed"])
      .select("id"),
  );

  return { ...rowToWorkflow(inserted), previous_version_id: String(old.id) };
}

// ---------------------------------------------------------------------------
// Sources + ingest
// ---------------------------------------------------------------------------

async function listSources(orgId: string, limit: number) {
  const rows: Row[] = must(
    await getSupabase().from("connected_sources").select("*").eq("org_id", orgId).order("created_at", { ascending: false }).limit(limit),
  );
  return rows.map((r) => ({ ...r, config: redactConfig(r.config) }));
}

async function createSource(orgId: string, body: Row) {
  const sourceType = String(body.source_type || "");
  if (!SOURCE_TYPES.has(sourceType)) throw new HttpError(400, `unsupported source_type: ${sourceType}`);
  const config: Row = body.config && typeof body.config === "object" ? body.config : {};
  const missing = missingConfig(sourceType, config);
  if (missing.length) throw new HttpError(400, `missing required config keys for ${sourceType}: ${JSON.stringify(missing)}`);
  const row: Row = must(
    await getSupabase()
      .from("connected_sources")
      .insert({
        org_id: orgId,
        source_type: sourceType,
        display_name: String(body.display_name || sourceType),
        // Demo mode never talks to the provider, so don't keep real tokens.
        config: redactConfig(config),
        is_active: true,
        last_validated_at: nowIso(),
        last_validation_status: "valid",
      })
      .select("*")
      .single(),
  );
  return { ...row, config: redactConfig(row.config) };
}

async function updateSource(orgId: string, id: string, body: Row) {
  const supa = getSupabase();
  const found: Row[] = must(await supa.from("connected_sources").select("*").eq("id", id).eq("org_id", orgId).limit(1));
  if (!found.length) throw new HttpError(404, `source not found: ${id}`);
  const patch: Row = {};
  if (body.display_name != null) patch.display_name = String(body.display_name);
  if (body.config != null) {
    const missing = missingConfig(found[0].source_type, body.config);
    if (missing.length) throw new HttpError(400, `missing required config keys for ${found[0].source_type}: ${JSON.stringify(missing)}`);
    patch.config = redactConfig(body.config);
  }
  if (body.is_active != null) patch.is_active = !!body.is_active;
  if (!Object.keys(patch).length) throw new HttpError(400, "no fields to update");
  const rows: Row[] = must(await supa.from("connected_sources").update(patch).eq("id", id).eq("org_id", orgId).select("*"));
  return { ...rows[0], config: redactConfig(rows[0].config) };
}

async function deleteSource(orgId: string, id: string) {
  const rows: Row[] = must(await getSupabase().from("connected_sources").delete().eq("id", id).eq("org_id", orgId).select("id"));
  if (!rows.length) throw new HttpError(404, `source not found: ${id}`);
  return { status: "ok", deleted: id };
}

function validateConfig(sourceType: string, config: Row) {
  if (!SOURCE_TYPES.has(sourceType)) throw new HttpError(400, `unsupported source_type: ${sourceType}`);
  const missing = missingConfig(sourceType, config || {});
  return missing.length ? { valid: false, error: `Missing required fields: ${missing.join(", ")}` } : { valid: true, error: null };
}

async function validateSource(orgId: string, id: string) {
  const supa = getSupabase();
  const found: Row[] = must(await supa.from("connected_sources").select("*").eq("id", id).eq("org_id", orgId).limit(1));
  if (!found.length) throw new HttpError(404, `source not found: ${id}`);
  const when = nowIso();
  must(
    await supa
      .from("connected_sources")
      .update({ last_validated_at: when, last_validation_status: "valid", last_validation_error: null })
      .eq("id", id)
      .eq("org_id", orgId)
      .select("id"),
  );
  return { valid: true, error: null, validated_at: when };
}

async function latestRun(orgId: string): Promise<Row | null> {
  const rows: Row[] = must(
    await getSupabase().from("ingest_runs").select("*").eq("org_id", orgId).order("started_at", { ascending: false }).limit(1),
  );
  return rows[0] || null;
}

async function ingestStatus(orgId: string) {
  const last = await latestRun(orgId);
  const base = last ? Date.parse(last.started_at) : Date.now();
  return {
    last_run: last,
    next_run_at: new Date(base + SCHEDULE_HOURS * 3_600_000).toISOString(),
    schedule_hours: SCHEDULE_HOURS,
  };
}

/** Simulated ingest cycle: touches every active source, records a run, and
 *  sometimes "discovers" a new drift conflict on a demo workflow. */
async function triggerIngest(orgId: string) {
  const supa = getSupabase();
  const started = nowIso();
  const sources: Row[] = must(await supa.from("connected_sources").select("id").eq("org_id", orgId).eq("is_active", true));
  if (sources.length) {
    must(await supa.from("connected_sources").update({ last_synced_at: started }).eq("org_id", orgId).eq("is_active", true).select("id"));
  }

  let newConflicts = 0;
  if (sources.length && Math.random() < 0.6) {
    const skills = await activeSkills(orgId);
    const byKey = new Map<string, Row>();
    for (const s of skills) {
      const key = seedSkillKeyFor(s.process_name);
      if (key) byKey.set(key, s);
    }
    for (const c of DISCOVERABLE_CONFLICTS) {
      const s = byKey.get(c.skill);
      if (s && (await insertConflict(orgId, String(s.id), s.process_name, c))) {
        newConflicts = 1;
        break;
      }
    }
  }

  const n = sources.length;
  await supa.from("ingest_runs").insert({
    org_id: orgId,
    started_at: started,
    duration_seconds: n ? 4 + Math.floor(Math.random() * 12) : 0,
    sources_checked: n,
    new_chunks: n ? Math.floor(Math.random() * 14) : 0,
    skipped_chunks: n ? 20 + Math.floor(Math.random() * 40) : 0,
    new_conflicts: newConflicts,
    stale_flagged: 0,
    stale_cleared: 0,
    errors: [],
    errored: false,
  });
  return { triggered: true, message: "Ingest started in background" };
}

// ---------------------------------------------------------------------------
// Agent API: keys + skills/match
// ---------------------------------------------------------------------------

async function listKeys(orgId: string, limit: number) {
  const rows: Row[] = must(
    await getSupabase()
      .from("api_keys")
      .select("id,name,key_prefix,created_at,last_used_at,request_count,is_active")
      .eq("org_id", orgId)
      .order("created_at", { ascending: false })
      .limit(limit),
  );
  return rows.map((r) => ({
    id: String(r.id),
    name: r.name || "",
    prefix: r.key_prefix || "",
    created_at: r.created_at ?? null,
    last_used_at: r.last_used_at ?? null,
    request_count: r.request_count || 0,
    is_active: r.is_active !== false,
  }));
}

async function createKey(orgId: string, body: Row) {
  const name = String(body.name || "").trim();
  if (!name) throw new HttpError(422, "name is required");
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const plaintext = "fb_live_" + Buffer.from(bytes).toString("base64url");
  const prefix = plaintext.slice(0, 12);
  const row: Row = must(
    await getSupabase()
      .from("api_keys")
      .insert({ org_id: orgId, name, key_prefix: prefix, key_hash: bcrypt.hashSync(plaintext, 10), is_active: true })
      .select("id")
      .single(),
  );
  return { id: String(row.id), prefix, key: plaintext, name };
}

async function revokeKey(orgId: string, id: string) {
  const rows: Row[] = must(
    await getSupabase().from("api_keys").update({ is_active: false }).eq("id", id).eq("org_id", orgId).select("id"),
  );
  if (!rows.length) throw new HttpError(404, `API key not found: ${id}`);
  return { status: "ok", revoked: id };
}

function recencyScore(iso: string | null): number {
  const days = iso ? Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 86_400_000)) : null;
  if (days === null || Number.isNaN(days)) return 0.1;
  if (days <= 30) return 1.0;
  if (days <= 90) return 0.7;
  if (days <= 180) return 0.4;
  return 0.1;
}

function freshness(lastConfirmed: string | null) {
  if (!lastConfirmed) return { last_confirmed_at: null, days_since_confirmed: null, source_freshness: null, freshness_warning: null };
  const days = Math.max(0, Math.floor((Date.now() - Date.parse(lastConfirmed)) / 86_400_000));
  if (days < 30) return { last_confirmed_at: lastConfirmed, days_since_confirmed: days, source_freshness: "fresh", freshness_warning: null };
  if (days < 90) {
    return {
      last_confirmed_at: lastConfirmed,
      days_since_confirmed: days,
      source_freshness: "aging",
      freshness_warning: `This workflow was last reviewed ${days} days ago. Consider verifying before acting on it.`,
    };
  }
  return {
    last_confirmed_at: lastConfirmed,
    days_since_confirmed: days,
    source_freshness: "stale",
    freshness_warning: `This workflow has not been reviewed in ${days} days and may be outdated. Escalate to a human rather than acting automatically.`,
  };
}

async function matchSkill(orgId: string, q: string) {
  const started = Date.now();
  if (q.trim().length < 2) throw new HttpError(422, "q must be at least 2 characters");
  const skills = await activeSkills(orgId);
  const scored = skills
    .map((s) => {
      const doc = [
        s.process_name, s.process_name, s.description, s.process_trigger,
        ...(s.decision_rules || []), ...(s.approvals || []), ...(s.exceptions || []),
        ...(s.steps || []).map((st: Row) => st.action),
      ].join(" ");
      const sim = keywordSimilarity(q, doc);
      const rec = recencyScore(s.reviewed_at || s.generated_at);
      return { s, sim, rec, combined: sim * 0.7 + rec * 0.3 };
    })
    .sort((a, b) => b.sim - a.sim);

  const relevant = scored.filter((x) => x.sim >= 0.4).sort((a, b) => b.combined - a.combined);
  if (!relevant.length) {
    return json(
      {
        error: `No workflow matched query '${q}' above the confidence threshold.`,
        code: "SKILL_NOT_FOUND",
        docs: DOCS_URL,
        suggestions: scored.slice(0, 3).map((x) => ({ id: String(x.s.id), process: x.s.process_name, similarity_score: Number(x.sim.toFixed(4)) })),
      },
      404,
    );
  }

  const top = relevant[0];
  // Log against the org's newest active key so the usage chart moves.
  const supa = getSupabase();
  const { data: keys } = await supa
    .from("api_keys")
    .select("id,request_count")
    .eq("org_id", orgId)
    .eq("is_active", true)
    .order("created_at", { ascending: false })
    .limit(1);
  if (keys && keys.length) {
    await supa.from("api_requests").insert({
      api_key_id: keys[0].id,
      org_id: orgId,
      endpoint: "/api/v1/skills/match",
      query_text: q.slice(0, 2000),
      matched_skill_id: top.s.id,
      response_time_ms: Date.now() - started + 80,
    });
    await supa.from("api_keys").update({ request_count: (keys[0].request_count || 0) + 1, last_used_at: nowIso() }).eq("id", keys[0].id);
  }

  return json({
    matched: true,
    confidence: top.sim >= 0.75 ? "high" : "medium",
    similarity_score: Number(top.sim.toFixed(4)),
    recency_score: Number(top.rec.toFixed(4)),
    combined_score: Number(top.combined.toFixed(4)),
    query: q,
    skill: rowToAgentSkill(top.s),
    ...freshness(top.s.reviewed_at || top.s.generated_at),
  });
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

function intParam(params: URLSearchParams, name: string, fallback: number, max = 200): number {
  const n = parseInt(params.get(name) || "", 10);
  return Number.isFinite(n) && n >= 1 ? Math.min(n, max) : fallback;
}

async function route(method: string, path: string, params: URLSearchParams, orgId: string, body: Row): Promise<Response | unknown> {
  const seg = path.split("/").filter(Boolean).map(decodeURIComponent);
  const is = (m: string, ...parts: (string | null)[]) =>
    method === m && seg.length === parts.length && parts.every((p, i) => p === null || p === seg[i]);

  // Endpoints that don't need a tenant.
  if (is("GET", "health")) return { status: "ok", version: "mock" };
  if (is("GET", "demo", null)) {
    const text = DEMO_CONTENT[seg[1]];
    if (!text) throw new HttpError(404, `demo not found: ${seg[1]}`);
    return new Response(text, { headers: { "content-type": "text/plain; charset=utf-8" } });
  }
  if (is("POST", "setup")) {
    const name = String(body.company_name || "").trim();
    if (!name) throw new HttpError(400, "company_name is required");
    const org = await createOrganisation(name.slice(0, 200));
    await ensureSeeded(org.id);
    return org;
  }

  if (!orgId) throw new HttpError(401, "No organisation in request.");
  await ensureSeeded(orgId);

  if (is("POST", "workflows", "generate")) return generateWorkflow(orgId, body);
  if (is("GET", "workflows", null)) return getWorkflow(orgId, seg[1]);
  if (is("GET", "history")) return history(orgId, intParam(params, "limit", 5));
  if (is("DELETE", "history")) return clearHistory(orgId);

  if (is("GET", "conflicts")) return listConflicts(orgId, params.get("include_snoozed") === "true", intParam(params, "limit", 50));
  if (is("POST", "conflicts", null, "resolve")) return resolveConflict(orgId, seg[1], body);
  if (is("GET", "skills", null, "conflicts")) return skillConflicts(orgId, seg[1], intParam(params, "limit", 50));
  if (is("POST", "skills", null, "review")) return markReviewed(orgId, seg[1]);

  if (is("GET", "ingest", "status")) return ingestStatus(orgId);
  if (is("POST", "ingest", "trigger")) return triggerIngest(orgId);

  if (is("GET", "sources")) return listSources(orgId, intParam(params, "limit", 50));
  if (is("POST", "sources")) return createSource(orgId, body);
  if (is("POST", "sources", "validate")) return validateConfig(String(body.source_type || ""), body.config || {});
  if (is("PATCH", "sources", null)) return updateSource(orgId, seg[1], body);
  if (is("DELETE", "sources", null)) return deleteSource(orgId, seg[1]);
  if (is("POST", "sources", null, "validate")) return validateSource(orgId, seg[1]);

  if (is("GET", "api", "v1", "keys")) return listKeys(orgId, intParam(params, "limit", 50));
  if (is("POST", "api", "v1", "keys")) return createKey(orgId, body);
  if (is("DELETE", "api", "v1", "keys", null)) return revokeKey(orgId, seg[3]);
  if (is("GET", "api", "v1", "skills", "match")) return matchSkill(orgId, params.get("q") || "");

  throw new HttpError(404, `mock backend has no route for ${method} ${path}`);
}

/** Handle a backend request in-process. Mirrors fetch()'s contract: never
 *  throws, always resolves to a Response with the FastAPI error envelope. */
export async function handleMockRequest(path: string, init?: RequestInit): Promise<Response> {
  const url = new URL(path, "http://mock.local");
  const method = (init?.method || "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  const orgId = headers.get("x-org-id") || "";

  let body: Row = {};
  if (typeof init?.body === "string" && init.body.trim()) {
    try {
      body = JSON.parse(init.body);
    } catch {
      return json({ error: "Invalid JSON body", code: "INVALID_REQUEST", docs: DOCS_URL }, 422);
    }
  }

  try {
    const result = await route(method, url.pathname, url.searchParams, orgId, body);
    return result instanceof Response ? result : json(result);
  } catch (err) {
    const status = err instanceof HttpError ? err.status : 500;
    const message = err instanceof Error ? err.message : String(err);
    return json({ error: status === 500 ? `Mock backend error: ${message}` : message, code: codeFor(status), docs: DOCS_URL }, status);
  }
}
