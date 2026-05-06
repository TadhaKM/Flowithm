"use client";

// Knowledge-base detail page. Reuses the two-panel render from /workflow/[id]
// and adds: inline name editing, Re-extract (re-runs generation against the
// stored raw_text), Mark as reviewed, Archive (with confirmation).
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type WorkflowStep = {
  step: number;
  action: string;
  owner: string;
  notes: string;
};

type Workflow = {
  id: string;
  process: string;
  description: string;
  trigger: string;
  steps: WorkflowStep[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
  source: string;
  source_metadata: Record<string, unknown>;
  raw_text: string;
  archived: boolean;
  archived_at?: string | null;
  reviewed_at?: string | null;
  generated_at?: string | null;
};

export default function BrainDetail() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const router = useRouter();

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Edit state
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [savingName, setSavingName] = useState(false);

  // Action state
  const [reExtracting, setReExtracting] = useState(false);
  const [marking, setMarking] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/brain/${id}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        return res.json();
      })
      .then((data: Workflow) => {
        if (!cancelled) {
          setWorkflow(data);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!actionMsg) return;
    const t = setTimeout(() => setActionMsg(null), 2500);
    return () => clearTimeout(t);
  }, [actionMsg]);

  async function copyJson() {
    if (!workflow) return;
    try {
      await navigator.clipboard.writeText(workflowToJson(workflow));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  }

  async function saveName() {
    if (!workflow || !draftName.trim() || savingName) return;
    setSavingName(true);
    try {
      const res = await fetch(`/api/brain/${workflow.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ process_name: draftName.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = (await res.json()) as Workflow;
      setWorkflow(updated);
      setEditing(false);
      setActionMsg("Saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingName(false);
    }
  }

  async function reExtract() {
    if (!workflow || reExtracting) return;
    if (!workflow.raw_text) {
      setActionMsg("No raw source stored — re-extract isn't available for this one.");
      return;
    }
    setReExtracting(true);
    try {
      const res = await fetch(`${API_URL}/workflows/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: workflow.process,
          content: workflow.raw_text,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = (await res.json()) as Workflow;
      // Re-extract creates a new row. Push to its detail page.
      if (fresh.id) {
        router.push(`/brain/${fresh.id}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReExtracting(false);
    }
  }

  async function markReviewed() {
    if (!workflow || marking) return;
    setMarking(true);
    try {
      const res = await fetch(`/api/brain/${workflow.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewed_at: "now" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated = (await res.json()) as Workflow;
      setWorkflow(updated);
      setActionMsg("Marked as reviewed");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMarking(false);
    }
  }

  async function archive() {
    if (!workflow || archiving) return;
    if (!confirmArchive) {
      setConfirmArchive(true);
      setTimeout(() => setConfirmArchive(false), 2500);
      return;
    }
    setConfirmArchive(false);
    setArchiving(true);
    try {
      const res = await fetch(`/api/brain/${workflow.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      router.push("/brain");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setArchiving(false);
    }
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <header className="mb-8 flex items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <Link
              href="/"
              className="text-base font-medium tracking-tight text-zinc-100 hover:text-zinc-300 transition-colors"
            >
              Flowithm
            </Link>
            <Link
              href="/brain"
              className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors"
            >
              Knowledge base
            </Link>
          </div>
          {workflow?.reviewed_at && (
            <span className="text-[10px] uppercase tracking-wider font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 rounded-full px-2 py-0.5">
              Reviewed
            </span>
          )}
        </header>

        {loading && (
          <div className="text-zinc-500 text-sm flex items-center gap-2">
            <Spinner /> Loading workflow…
          </div>
        )}

        {error && !loading && (
          <div className="bg-rose-950/40 border border-rose-900/60 text-rose-200 rounded-xl p-4 text-sm">
            <strong className="font-medium">Couldn't load this workflow.</strong>{" "}
            {error}
          </div>
        )}

        {workflow && !loading && !error && (
          <>
            <nav className="text-xs text-zinc-500 mb-3">
              <Link href="/brain" className="hover:text-zinc-300 transition-colors">
                Knowledge base
              </Link>
              <span className="mx-2 text-zinc-700">/</span>
              <span className="text-zinc-300">{workflow.process}</span>
            </nav>

            {editing ? (
              <div className="mb-4 flex items-center gap-2">
                <input
                  autoFocus
                  type="text"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveName();
                    if (e.key === "Escape") setEditing(false);
                  }}
                  className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xl font-medium text-zinc-100 focus:outline-none focus:border-[#1D9E75]/60 transition-colors"
                />
                <button
                  onClick={saveName}
                  disabled={savingName || !draftName.trim()}
                  className="text-sm bg-[#1D9E75] hover:bg-[#22b384] disabled:bg-zinc-800 text-white px-3 py-2 rounded-lg transition-colors"
                >
                  {savingName ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="text-sm bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 px-3 py-2 rounded-lg transition-colors"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <h1 className="text-2xl font-medium text-zinc-100 mb-4">
                {workflow.process}
              </h1>
            )}

            <div className="mb-6 flex flex-wrap items-center gap-2">
              {!editing && (
                <button
                  onClick={() => {
                    setDraftName(workflow.process);
                    setEditing(true);
                  }}
                  className="text-xs bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-zinc-100 px-3 py-1.5 rounded-md transition-colors"
                >
                  Edit name
                </button>
              )}
              <button
                onClick={reExtract}
                disabled={reExtracting || !workflow.raw_text}
                title={
                  !workflow.raw_text
                    ? "No raw source stored — re-extract unavailable"
                    : "Re-run generation against the original source"
                }
                className="text-xs bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-zinc-100 px-3 py-1.5 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {reExtracting ? "Re-extracting…" : "Re-extract"}
              </button>
              <button
                onClick={markReviewed}
                disabled={marking}
                className="text-xs bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-zinc-100 px-3 py-1.5 rounded-md transition-colors disabled:opacity-50"
              >
                {marking ? "Saving…" : "Mark as reviewed"}
              </button>
              <button
                onClick={archive}
                disabled={archiving}
                className={`text-xs px-3 py-1.5 rounded-md transition-colors disabled:opacity-50 ${
                  confirmArchive
                    ? "bg-amber-500/15 border border-amber-500/30 text-amber-200 hover:text-amber-100"
                    : "bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-zinc-100"
                }`}
              >
                {archiving
                  ? "Archiving…"
                  : confirmArchive
                    ? "Click again to confirm"
                    : "Archive"}
              </button>
              {actionMsg && (
                <span className="text-xs text-emerald-400">{actionMsg}</span>
              )}
            </div>

            {workflow.archived && (
              <div className="mb-4 bg-zinc-900 border border-zinc-800 text-zinc-400 rounded-xl px-4 py-2.5 text-xs">
                This workflow is archived.
              </div>
            )}

            <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 animate-fade-in">
              <WorkflowPanel workflow={workflow} />
              <SkillsFilePanel
                workflow={workflow}
                copied={copied}
                onCopy={copyJson}
              />
            </section>

            <SourceFooter workflow={workflow} />
          </>
        )}
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------
// Panels (self-contained — see /workflow/[id]/page.tsx for the rationale)
// --------------------------------------------------------------------------

function WorkflowPanel({ workflow }: { workflow: Workflow }) {
  return (
    <article className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium mb-3">
        Workflow
      </div>
      {workflow.trigger && (
        <div className="mt-1 rounded-lg border border-zinc-800 bg-zinc-950/45 px-3 py-2.5">
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium">
            Trigger
          </div>
          <p className="mt-1 text-sm text-zinc-300">{workflow.trigger}</p>
        </div>
      )}

      {workflow.steps.length > 0 && (
        <div className="mt-6">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium mb-4">
            Execution Steps
          </h3>
          <ol className="space-y-4">
            {workflow.steps.map((s) => (
              <li
                key={s.step}
                className="flex gap-3 rounded-lg border border-zinc-800 bg-zinc-950/35 p-3.5"
              >
                <span className="flex items-center justify-center shrink-0 w-7 h-7 rounded-full bg-[#1D9E75]/15 border border-[#1D9E75]/35 text-[12px] font-semibold text-emerald-300">
                  {s.step}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-zinc-100">
                    {s.action}
                  </p>
                  {s.notes && (
                    <div className="mt-2 border-l-2 border-[#1D9E75] bg-zinc-900/80 pl-3 pr-3 py-2 text-[13px] text-zinc-300 rounded-r">
                      {s.notes}
                    </div>
                  )}
                  {s.owner && s.owner !== "unspecified" && (
                    <p className="mt-2 text-xs font-medium text-zinc-300">
                      Owner: {s.owner}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {workflow.decision_rules.length > 0 && (
        <div className="mt-6">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium mb-3">
            Decision Rules
          </h3>
          <div className="space-y-2">
            {workflow.decision_rules.map((rule, i) => (
              <div
                key={i}
                className="border-l-2 border-[#1D9E75] bg-zinc-800/60 pl-3 pr-3 py-2 text-[13px] text-zinc-200 rounded-r"
              >
                {rule}
              </div>
            ))}
          </div>
        </div>
      )}

      {workflow.approvals.length > 0 && (
        <div className="mt-6">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium mb-3">
            Approvals
          </h3>
          <div className="space-y-2">
            {workflow.approvals.map((approval, i) => (
              <div
                key={i}
                className="border-l-2 border-amber-500 bg-amber-500/5 pl-3 pr-3 py-2 text-[13px] text-amber-100/90 rounded-r"
              >
                {approval}
              </div>
            ))}
          </div>
        </div>
      )}

      {workflow.exceptions.length > 0 && (
        <div className="mt-6">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium mb-3">
            Exceptions
          </h3>
          <div className="flex flex-wrap gap-2">
            {workflow.exceptions.map((exc, i) => (
              <span
                key={i}
                className="text-xs bg-zinc-800 text-zinc-400 border border-zinc-700/60 px-2.5 py-1 rounded-full"
              >
                {exc}
              </span>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}

function SkillsFilePanel({
  workflow,
  copied,
  onCopy,
}: {
  workflow: Workflow;
  copied: boolean;
  onCopy: () => void;
}) {
  const json = workflowToJson(workflow);
  return (
    <article className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium">
            Skills file
          </span>
          <span className="text-[10px] uppercase tracking-wider font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 rounded-full px-2 py-0.5">
            Agent-ready
          </span>
        </div>
        <button
          onClick={onCopy}
          className="text-xs bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 px-2.5 py-1.5 rounded-md transition-colors"
        >
          {copied ? "Copied!" : "Copy JSON"}
        </button>
      </div>
      <pre
        className="font-mono text-[12.5px] leading-relaxed bg-zinc-950 border border-zinc-800 rounded-lg p-4 text-zinc-300 overflow-auto max-h-[520px] whitespace-pre-wrap break-words scrollbar-thin"
        dangerouslySetInnerHTML={{ __html: highlightWorkflowJson(json) }}
      />
      {workflow.sources.length > 0 && (
        <p className="mt-4 text-xs text-zinc-500">
          <span className="text-zinc-600">Sources:</span>{" "}
          {workflow.sources.join(", ")}
        </p>
      )}
    </article>
  );
}

function SourceFooter({ workflow }: { workflow: Workflow }) {
  const meta = workflow.source_metadata || {};
  const generated = workflow.generated_at
    ? new Date(workflow.generated_at).toLocaleString()
    : "";
  let detail = "";
  if (workflow.source === "slack") {
    const channel = (meta.channel_name as string) || "channel";
    const messageCount = (meta.message_count as number) || 0;
    detail = `From #${channel}${messageCount ? ` • ${messageCount} messages` : ""}`;
  } else if (workflow.source === "notion") {
    const page = (meta.page_title as string) || "page";
    detail = `From page: ${page}`;
  } else {
    detail = "Pasted manually from the Flowithm UI";
  }
  return (
    <footer className="mt-6 text-xs text-zinc-500 flex items-center gap-3">
      <span className="capitalize text-zinc-400">{workflow.source}</span>
      <span className="text-zinc-700">·</span>
      <span>Extracted on {generated}</span>
      <span className="text-zinc-700">·</span>
      <span>{detail}</span>
    </footer>
  );
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function workflowToJson(wf: Workflow): string {
  const { id, generated_at, source, source_metadata, raw_text, archived, archived_at, reviewed_at, ...payload } = wf;
  void id; void generated_at; void source; void source_metadata; void raw_text; void archived; void archived_at; void reviewed_at;
  return JSON.stringify(payload, null, 2);
}

function highlightWorkflowJson(json: string): string {
  const escaped = json
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped.replace(
    /"(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?/g,
    (match) => {
      const isKey = /:\s*$/.test(match);
      const color = isKey ? "#34d399" : "#c4b5fd";
      return `<span style="color:${color}">${match}</span>`;
    },
  );
}

function Spinner() {
  return (
    <svg className="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
