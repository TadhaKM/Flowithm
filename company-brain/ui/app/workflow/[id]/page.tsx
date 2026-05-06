"use client";

// Workflow deeplink viewer: GET /workflows/{id}, render the same two-panel
// layout as the home page output section. Self-contained on purpose —
// the home page is heavily customized and we don't want a refactor in its
// path. If you change the home-page workflow rendering and want it
// mirrored here, copy the change across both files.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type WorkflowStep = {
  step: number;
  action: string;
  owner: string;
  notes: string;
};

type Workflow = {
  id?: string;
  process: string;
  description: string;
  trigger: string;
  steps: WorkflowStep[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
  source?: string;
  source_metadata?: Record<string, unknown>;
  archived?: boolean;
  archived_at?: string | null;
  generated_at?: string;
};

export default function WorkflowDeeplink() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/workflows/${id}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text();
          throw new Error(`HTTP ${res.status}: ${body || "not found"}`);
        }
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

  async function copyJson() {
    if (!workflow) return;
    const json = workflowToJson(workflow);
    try {
      await navigator.clipboard.writeText(json);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore — clipboard may be blocked over plain http
    }
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <header className="mb-10">
          <div className="flex items-center justify-between gap-4">
            <Link
              href="/"
              className="text-base font-medium tracking-tight text-zinc-100 hover:text-zinc-300 transition-colors"
            >
              Flowithm
            </Link>
            <p className="text-sm text-zinc-500 hidden sm:block">
              Workflow detail
            </p>
          </div>
          <p className="mt-2 text-xs text-zinc-600 font-mono">{id}</p>
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
            {workflow.archived && (
              <div className="mb-4 bg-zinc-900 border border-zinc-800 text-zinc-400 rounded-xl px-4 py-2.5 text-xs">
                This workflow is archived. A newer version may exist.
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
            {workflow.source === "slack" && (
              <SlackProvenanceFooter workflow={workflow} />
            )}
          </>
        )}
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------
// Panels
// --------------------------------------------------------------------------

function WorkflowPanel({ workflow }: { workflow: Workflow }) {
  return (
    <article className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium mb-3">
        Workflow
      </div>
      <h2 className="text-xl font-medium text-zinc-100">{workflow.process}</h2>
      {workflow.trigger && (
        <div className="mt-5 rounded-lg border border-zinc-800 bg-zinc-950/45 px-3 py-2.5">
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

function SlackProvenanceFooter({ workflow }: { workflow: Workflow }) {
  const meta = workflow.source_metadata || {};
  const channel = (meta.channel_name as string) || "unknown channel";
  const messageCount = (meta.message_count as number) || 0;
  const triggeredBy = (meta.triggered_by as string) || "";
  return (
    <footer className="mt-6 text-xs text-zinc-500">
      Extracted from #{channel}
      {messageCount ? ` • ${messageCount} messages` : ""}
      {triggeredBy ? ` • triggered by ${triggeredBy}` : ""}
    </footer>
  );
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function workflowToJson(wf: Workflow): string {
  const {
    id: _id,
    generated_at: _gen,
    source: _source,
    source_metadata: _meta,
    archived: _archived,
    archived_at: _archivedAt,
    ...payload
  } = wf;
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
    <svg
      className="animate-spin"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeOpacity="0.25"
        strokeWidth="3"
      />
      <path
        d="M22 12a10 10 0 0 1-10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
