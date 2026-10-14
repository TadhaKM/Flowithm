// Demo dataset for the mock backend. Every new organisation (and any
// existing one with no skills and no ingest history) gets a realistic
// Loopline knowledge base so the dashboard has something to explore:
// workflows, drift conflicts, connected sources, an ingest run, an API key
// and two weeks of agent traffic for the usage chart.
import bcrypt from "bcryptjs";

import { getSupabase } from "@/lib/supabase";

const DAY = 86_400_000;

function daysAgo(n: number, hourJitter = 0): string {
  return new Date(Date.now() - n * DAY - hourJitter * 3_600_000).toISOString();
}

type SeedSkill = {
  key: string;
  process_name: string;
  description: string;
  process_trigger: string;
  steps: { step: number; action: string; owner: string; notes: string }[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
  source: string;
  source_metadata: Record<string, unknown>;
  generated_days_ago: number;
  reviewed_days_ago?: number;
  needs_review?: string;
};

const SKILLS: SeedSkill[] = [
  {
    key: "refund",
    process_name: "Customer refund handling",
    description: "How Customer Success decides, approves and processes refund requests.",
    process_trigger: "A customer asks for a refund on any paid Loopline plan.",
    steps: [
      { step: 1, action: "Confirm the customer has been charged at least once", owner: "Support rep", notes: "Trial-only accounts: cancel the trial, no refund needed." },
      { step: 2, action: "Check the charge age using Stripe's Charge.created timestamp", owner: "Support rep", notes: "Not subscriptions.created_at — they can differ by minutes." },
      { step: 3, action: "Check for prior refunds on any earlier subscription", owner: "Support rep", notes: "Repeat refunds are a fraud signal." },
      { step: 4, action: "Get approval if the amount or history requires it", owner: "CS lead", notes: "DM the CS lead with the reason and Stripe charge ID." },
      { step: 5, action: "Process the refund in Stripe and tag the ticket refund-processed", owner: "Support rep", notes: "" },
    ],
    decision_rules: [
      "Refunds within 30 days of the first paid charge are approved by default.",
      "After 30 days, refunds are discretionary and capped at a partial refund of the unused period.",
      "Refunds over $500 require CS lead approval before processing.",
    ],
    approvals: [
      "CS lead (Alex Morgan) for any refund over $500 or any repeat refund.",
      "CEO sign-off for refunds over $5,000.",
    ],
    exceptions: [
      "Enterprise / VIP customers are always approved regardless of window or amount.",
      "Refunds caused by a confirmed Loopline outage are approved in full.",
    ],
    sources: ["notion:Refund Policy — Internal", "slack:#cs-leads"],
    source: "notion",
    source_metadata: { page: "Refund Policy — Internal" },
    generated_days_ago: 12,
    reviewed_days_ago: 4,
  },
  {
    key: "incident",
    process_name: "Production incident response",
    description: "What on-call does when a P1 alert fires on api-prod.",
    process_trigger: "PagerDuty fires a P1 alert for an api-prod service.",
    steps: [
      { step: 1, action: "Acknowledge the page within 5 minutes", owner: "On-call engineer", notes: "Unacked pages escalate to the secondary after 10 minutes." },
      { step: 2, action: "Open an incident channel and name an incident commander", owner: "On-call engineer", notes: "#inc-YYYYMMDD-short-name" },
      { step: 3, action: "Check Datadog for the failing service and recent deploys", owner: "On-call engineer", notes: "Runbook: notion.so/loopline/runbooks/api-latency" },
      { step: 4, action: "Roll back the last deploy if it landed within 2 hours of the alert", owner: "On-call engineer", notes: "" },
      { step: 5, action: "Post status page updates until resolved", owner: "Incident commander", notes: "" },
      { step: 6, action: "Write a postmortem within 3 business days", owner: "Incident commander", notes: "Blameless template in Notion." },
    ],
    decision_rules: [
      "If a deploy landed within 2 hours of the alert, roll back first and investigate after.",
      "Status page updates go out every 30 minutes during a P1.",
      "Database failover requires two engineers on the call.",
    ],
    approvals: ["CTO approval before any manual production database write."],
    exceptions: ["Third-party outages (Stripe, AWS) skip the rollback step and go straight to status page comms."],
    sources: ["slack:#engineering-incidents", "notion:Runbook — API latency"],
    source: "slack",
    source_metadata: { channel: "#engineering-incidents" },
    generated_days_ago: 21,
    reviewed_days_ago: 9,
  },
  {
    key: "onboarding",
    process_name: "Engineering onboarding",
    description: "The first two weeks for every new engineering hire.",
    process_trigger: "A new engineer's start date is confirmed.",
    steps: [
      { step: 1, action: "Ship the laptop so it arrives two days before the start date", owner: "IT", notes: "" },
      { step: 2, action: "Assign an onboarding buddy from a different squad", owner: "Manager", notes: "" },
      { step: 3, action: "Grant GitHub, Datadog and staging access on day one", owner: "IT", notes: "Production access waits until week 2." },
      { step: 4, action: "Pair on a small production change in week one", owner: "Onboarding buddy", notes: "" },
      { step: 5, action: "Hold a 30-day check-in", owner: "Manager", notes: "" },
    ],
    decision_rules: [
      "Production access is granted only after the new hire completes the security training.",
      "New hires join the on-call rotation no earlier than week 6.",
    ],
    approvals: ["Engineering manager approves production access."],
    exceptions: ["Contractors get staging access only and never join on-call."],
    sources: ["notion:Engineering Onboarding — Internal Handbook"],
    source: "notion",
    source_metadata: { page: "Engineering Onboarding — Internal Handbook" },
    generated_days_ago: 47,
    reviewed_days_ago: 40,
  },
  {
    key: "discount",
    process_name: "Enterprise discount approval",
    description: "Who can approve discounts on enterprise deals and how far they can go.",
    process_trigger: "A sales rep wants to offer a discount on an annual enterprise contract.",
    steps: [
      { step: 1, action: "Log the requested discount and deal size in the CRM opportunity", owner: "Sales rep", notes: "" },
      { step: 2, action: "Request approval in #deal-desk with the justification", owner: "Sales rep", notes: "" },
      { step: 3, action: "Apply the approved discount to the order form", owner: "Finance", notes: "" },
    ],
    decision_rules: [
      "Discounts up to 10% can be approved by the sales lead.",
      "Discounts between 10% and 20% need Finance approval.",
      "Discounts over 20% need CEO approval and are only offered on multi-year terms.",
    ],
    approvals: ["Sales lead up to 10%.", "Finance for 10–20%.", "CEO above 20%."],
    exceptions: ["Non-profits get a standing 15% discount with no approval needed."],
    sources: ["gmail:label:deal-desk"],
    source: "gmail",
    source_metadata: { label: "deal-desk" },
    generated_days_ago: 128,
    needs_review: "Auto-flagged: 128 days since last confirmation.",
  },
  {
    key: "bugs",
    process_name: "Customer bug triage",
    description: "How support routes customer-reported bugs to engineering.",
    process_trigger: "A customer reports something that looks like a product bug.",
    steps: [
      { step: 1, action: "Reproduce the bug on staging with the customer's steps", owner: "Support rep", notes: "" },
      { step: 2, action: "File a GitHub issue with the bug label and the Intercom link", owner: "Support rep", notes: "" },
      { step: 3, action: "Set severity using the triage matrix", owner: "Engineer", notes: "" },
      { step: 4, action: "Reply to the customer with the expected timeline", owner: "Support rep", notes: "" },
    ],
    decision_rules: [
      "Sev1 bugs (data loss or a blocked workflow for a paying customer) are fixed within 24 hours.",
      "Sev3 bugs go into the next sprint's backlog.",
    ],
    approvals: [],
    exceptions: ["Bugs reported by enterprise customers are bumped one severity level."],
    sources: ["intercom:tag:escalated", "github:loopline/app"],
    source: "manual",
    source_metadata: {},
    generated_days_ago: 3,
  },
];

type SeedConflict = {
  skill: string;
  conflict_type: "contradiction" | "update" | "expansion" | "deprecation";
  severity: "high" | "medium" | "low";
  conflict_description: string;
  existing_rule: string;
  new_evidence: string;
  suggested_update: string;
  days_ago: number;
};

const CONFLICTS: SeedConflict[] = [
  {
    skill: "refund",
    conflict_type: "contradiction",
    severity: "high",
    conflict_description: "The CS lead approval threshold for refunds was lowered in Slack but the workflow still says $500.",
    existing_rule: "Refunds over $500 require CS lead approval before processing.",
    new_evidence: "#cs-leads, Alex Morgan: \"Starting this week any refund over $250 needs my sign-off — finance asked us to tighten this after Q3.\"",
    suggested_update: "Refunds over $250 require CS lead approval before processing.",
    days_ago: 1,
  },
  {
    skill: "incident",
    conflict_type: "update",
    severity: "medium",
    conflict_description: "Recent incidents posted status updates every 15 minutes, not every 30.",
    existing_rule: "Status page updates go out every 30 minutes during a P1.",
    new_evidence: "#engineering-incidents postmortem: \"Customers complained 30 min was too slow — we're moving to 15-minute status updates for P1s.\"",
    suggested_update: "Status page updates go out every 15 minutes during a P1.",
    days_ago: 2,
  },
  {
    skill: "onboarding",
    conflict_type: "expansion",
    severity: "low",
    conflict_description: "Notion now says laptop shipping is handled by an external vendor.",
    existing_rule: "Ship the laptop so it arrives two days before the start date",
    new_evidence: "Notion, IT page (edited 5 days ago): \"Laptops are now provisioned and shipped by Workwize. Submit the request 10 days before start.\"",
    suggested_update: "Request the laptop from Workwize 10 days before the start date so it arrives two days early",
    days_ago: 5,
  },
];

// Extra conflicts the mock ingest cycle can "discover" on Sync now.
export const DISCOVERABLE_CONFLICTS: SeedConflict[] = [
  {
    skill: "discount",
    conflict_type: "contradiction",
    severity: "high",
    conflict_description: "A deal-desk email says Finance can now approve discounts up to 25%.",
    existing_rule: "Discounts between 10% and 20% need Finance approval.",
    new_evidence: "Gmail (deal-desk): \"Effective Q4, Finance can sign off on anything up to 25%. Above that still goes to Maya.\"",
    suggested_update: "Discounts between 10% and 25% need Finance approval.",
    days_ago: 0,
  },
  {
    skill: "bugs",
    conflict_type: "update",
    severity: "medium",
    conflict_description: "Engineering moved Sev1 bug SLAs from 24 hours to 12 hours.",
    existing_rule: "Sev1 bugs (data loss or a blocked workflow for a paying customer) are fixed within 24 hours.",
    new_evidence: "GitHub discussion #412: \"New SLA for Sev1: fix or mitigation within 12h. Updating the triage matrix.\"",
    suggested_update: "Sev1 bugs (data loss or a blocked workflow for a paying customer) are fixed or mitigated within 12 hours.",
    days_ago: 0,
  },
  {
    skill: "refund",
    conflict_type: "expansion",
    severity: "low",
    conflict_description: "Intercom shows annual-plan refunds being prorated, which the workflow doesn't cover.",
    existing_rule: "After 30 days, refunds are discretionary and capped at a partial refund of the unused period.",
    new_evidence: "Intercom (escalated): three annual-plan customers in October received prorated refunds for unused months.",
    suggested_update: "Annual plans cancelled after 30 days get a prorated refund for the unused full months.",
    days_ago: 0,
  },
];

const AGENT_QUERIES: [string, string][] = [
  ["customer wants a refund after 45 days", "refund"],
  ["refund request for $800 charge", "refund"],
  ["enterprise customer asking for refund", "refund"],
  ["api latency alert fired what do I do", "incident"],
  ["should I roll back the deploy", "incident"],
  ["new engineer starting monday", "onboarding"],
  ["can I give a 15% discount", "discount"],
  ["customer reported a bug in exports", "bugs"],
  ["how do I escalate a sev1 bug", "bugs"],
];

/** Insert a fresh organisation row, returning {id, name, slug, plan}. */
export async function createOrganisation(name: string) {
  const supa = getSupabase();
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "org";
  let slug = base;
  for (let i = 0; i < 5; i++) {
    const { data } = await supa.from("organisations").select("id").eq("slug", slug).limit(1);
    if (!data || data.length === 0) break;
    slug = `${base}-${Math.random().toString(16).slice(2, 8)}`;
  }
  const { data, error } = await supa
    .from("organisations")
    .insert({ name, slug, plan: "free" })
    .select("id,name,slug,plan")
    .single();
  if (error || !data) throw new Error(error?.message || "organisation insert failed");
  return { id: String(data.id), name: data.name as string, slug: data.slug as string, plan: (data.plan as string) || "free" };
}

/** Insert a conflict row for an existing skill. Returns false when the
 *  skill isn't active or the same conflict is already open. */
export async function insertConflict(orgId: string, skillId: string, processName: string, c: SeedConflict) {
  const supa = getSupabase();
  const { data: dup } = await supa
    .from("conflicts")
    .select("id")
    .eq("org_id", orgId)
    .eq("existing_skill_id", skillId)
    .eq("suggested_update", c.suggested_update)
    .limit(1);
  if (dup && dup.length > 0) return false;
  const { error } = await supa.from("conflicts").insert({
    org_id: orgId,
    existing_skill_id: skillId,
    existing_process_name: processName,
    conflict_type: c.conflict_type,
    conflict_description: c.conflict_description,
    existing_rule: c.existing_rule,
    new_evidence: c.new_evidence,
    suggested_update: c.suggested_update,
    severity: c.severity,
    status: "unresolved",
    created_at: daysAgo(c.days_ago, 2),
  });
  return !error;
}

export function seedSkillKeyFor(processName: string): string | null {
  const hit = SKILLS.find((s) => s.process_name.toLowerCase() === processName.toLowerCase());
  return hit ? hit.key : null;
}

async function seedOrganisation(orgId: string): Promise<void> {
  const supa = getSupabase();

  // Skills first: the (org_id, lower(process_name)) unique index makes this
  // batch fail if another instance is seeding the same org concurrently,
  // which doubles as a lock — the loser stops here.
  const { data: skillRows, error: skillErr } = await supa
    .from("skills")
    .insert(
      SKILLS.map((s) => ({
        org_id: orgId,
        process_name: s.process_name,
        description: s.description,
        process_trigger: s.process_trigger,
        steps: s.steps,
        decision_rules: s.decision_rules,
        approvals: s.approvals,
        exceptions: s.exceptions,
        sources: s.sources,
        source: s.source,
        source_metadata: s.source_metadata,
        raw_text: "",
        generated_at: daysAgo(s.generated_days_ago),
        reviewed_at: s.reviewed_days_ago !== undefined ? daysAgo(s.reviewed_days_ago) : null,
        needs_review: !!s.needs_review,
        needs_review_reason: s.needs_review || null,
        stale_flagged_at: s.needs_review ? daysAgo(1) : null,
        version: 1,
      })),
    )
    .select("id,process_name");
  if (skillErr || !skillRows) return;

  const idByKey = new Map<string, { id: string; name: string }>();
  for (const row of skillRows) {
    const key = seedSkillKeyFor(row.process_name as string);
    if (key) idByKey.set(key, { id: String(row.id), name: row.process_name as string });
  }

  for (const c of CONFLICTS) {
    const sk = idByKey.get(c.skill);
    if (sk) await insertConflict(orgId, sk.id, sk.name, c);
  }

  const syncedAt = daysAgo(0, 3);
  await supa.from("connected_sources").insert([
    {
      org_id: orgId,
      source_type: "slack",
      display_name: "Loopline Slack",
      config: { bot_token: "***", channel_ids: ["C01CSLEADS", "C02INCIDENTS"] },
      is_active: true,
      last_synced_at: syncedAt,
      last_validated_at: daysAgo(2),
      last_validation_status: "valid",
    },
    {
      org_id: orgId,
      source_type: "notion",
      display_name: "Notion — Policies & Runbooks",
      config: { integration_token: "***", page_ids: ["refund-policy", "runbook-api-latency", "eng-onboarding"] },
      is_active: true,
      last_synced_at: syncedAt,
      last_validated_at: daysAgo(2),
      last_validation_status: "valid",
    },
    {
      org_id: orgId,
      source_type: "gmail",
      display_name: "Deal desk inbox",
      config: { credentials_json: "***", label_filters: ["deal-desk", "policy"] },
      is_active: false,
      last_synced_at: daysAgo(9),
    },
  ]);

  await supa.from("ingest_runs").insert({
    org_id: orgId,
    started_at: syncedAt,
    duration_seconds: 14,
    sources_checked: 2,
    new_chunks: 12,
    skipped_chunks: 47,
    new_conflicts: 1,
    stale_flagged: 1,
    stale_cleared: 0,
    errors: [],
    errored: false,
  });

  // A demo API key plus two weeks of agent traffic for the usage chart.
  // The key_hash is a bcrypt hash of a random throwaway value — nobody
  // holds this key's plaintext; it only exists so the usage stats have
  // something to show, and it stays a valid hash for the real backend.
  const { data: key } = await supa
    .from("api_keys")
    .insert({
      org_id: orgId,
      name: "Support agent (demo)",
      key_prefix: "fb_live_demo",
      key_hash: bcrypt.hashSync(crypto.randomUUID(), 4),
      is_active: true,
      created_at: daysAgo(15),
    })
    .select("id")
    .single();
  if (!key) return;

  const requests: Record<string, unknown>[] = [];
  for (let d = 13; d >= 0; d--) {
    const perDay = 4 + Math.floor(Math.random() * 9) + (d < 5 ? 4 : 0);
    for (let i = 0; i < perDay; i++) {
      const [q, skillKey] = AGENT_QUERIES[Math.floor(Math.random() * AGENT_QUERIES.length)];
      requests.push({
        api_key_id: key.id,
        org_id: orgId,
        endpoint: "/api/v1/skills/match",
        query_text: q,
        matched_skill_id: idByKey.get(skillKey)?.id ?? null,
        response_time_ms: 90 + Math.floor(Math.random() * 260),
        created_at: daysAgo(d, Math.random() * 20),
      });
    }
  }
  await supa.from("api_requests").insert(requests);
  await supa
    .from("api_keys")
    .update({ request_count: requests.length, last_used_at: daysAgo(0, 1) })
    .eq("id", key.id);
}

const seeded = new Map<string, Promise<void>>();

/** Seed the demo dataset for an org that has never had any skills or
 *  ingest runs. Cheap after the first call per org per server instance. */
export function ensureSeeded(orgId: string): Promise<void> {
  if (!orgId) return Promise.resolve();
  let p = seeded.get(orgId);
  if (!p) {
    p = (async () => {
      const supa = getSupabase();
      const [{ count: skills }, { count: runs }] = await Promise.all([
        supa.from("skills").select("id", { count: "exact", head: true }).eq("org_id", orgId),
        supa.from("ingest_runs").select("id", { count: "exact", head: true }).eq("org_id", orgId),
      ]);
      if ((skills ?? 0) === 0 && (runs ?? 0) === 0) await seedOrganisation(orgId);
    })().catch(() => {
      // Never block a request on seeding; allow a retry on the next call.
      seeded.delete(orgId);
    });
    seeded.set(orgId, p);
  }
  return p;
}
