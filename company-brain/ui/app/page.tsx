"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const API_URL = "";

const DEMO_CHIPS = [
  {
    slug: "db_outage",
    label: "DB outage response",
    processName: "DB outage response",
  },
  {
    slug: "refund_policy",
    label: "Customer refund",
    processName: "Customer refund handling",
  },
  {
    slug: "onboarding",
    label: "New hire onboarding",
    processName: "Engineering onboarding",
  },
];

type WorkflowStep = {
  step: number;
  action: string;
  owner: string;
  notes: string;
};

type Workflow = {
  process: string;
  description: string;
  trigger: string;
  steps: WorkflowStep[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
  confidence?: number | string;
  generated_at?: string;
};

export default function Home() {
  const [content, setContent] = useState("");
  const [processName, setProcessName] = useState("");
  const [loading, setLoading] = useState(false);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState<Workflow[]>([]);
  const [chipLoading, setChipLoading] = useState<string | null>(null);
  const [clearingHistory, setClearingHistory] = useState(false);
  const [clearConfirm, setClearConfirm] = useState(false);

  useEffect(() => {
    fetchHistory();
  }, []);

  // Clear-confirm reset: first click arms the button; if no second click
  // within 2 seconds, it reverts to its idle label.
  useEffect(() => {
    if (!clearConfirm) return;
    const t = setTimeout(() => setClearConfirm(false), 2000);
    return () => clearTimeout(t);
  }, [clearConfirm]);

  async function fetchHistory() {
    try {
      const res = await fetch(`/api/workflows/history`);
      if (!res.ok) return;
      const data = (await res.json()) as Workflow[];
      setHistory(data);
    } catch {
      // History is non-critical; don't surface failures.
    }
  }

  async function loadChip(chip: (typeof DEMO_CHIPS)[number]) {
    if (chipLoading || loading) return;
    setChipLoading(chip.slug);
    setError(null);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/demo/${chip.slug}`);
      if (!res.ok)
        throw new Error(`Demo not found: ${chip.slug} (HTTP ${res.status})`);
      const text = await res.text();
      setContent(text);
      setProcessName(chip.processName);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChipLoading(null);
    }
  }

  async function generate() {
    if (!content.trim() || !processName.trim() || loading) return;
    setLoading(true);
    setError(null);
    setWorkflow(null);
    try {
      const res = await fetch(`/api/workflows/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: processName, content }),
      });
      if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
      const data = (await res.json()) as Workflow;
      setWorkflow(data);
      fetchHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function clearHistory() {
    if (clearingHistory) return;
    if (!clearConfirm) {
      // Arm the button — second click within 2s actually clears.
      setClearConfirm(true);
      return;
    }
    setClearConfirm(false);
    setClearingHistory(true);
    try {
      const res = await fetch(`/api/workflows/history`, { method: "DELETE" });
      if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
      setHistory([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearingHistory(false);
    }
  }

  async function copyJson() {
    if (!workflow) return;
    try {
      await copyToClipboard(workflowToJson(workflow));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore — clipboard might be blocked over http
    }
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <header className="mb-12">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-6">
              <h1 className="text-base font-medium tracking-tight text-zinc-100">
                Flowithm
              </h1>
              <Link
                href="/brain"
                className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors"
              >
                Knowledge base
              </Link>
            </div>
            <p className="text-sm text-zinc-400 hidden sm:block">
              Turn company knowledge into systems AI can run
            </p>
          </div>
          <p className="mt-3 max-w-2xl text-sm text-zinc-500">
            Most company knowledge lives in Slack, docs, and memory. Flowithm
            turns it into structured workflows.
          </p>
        </header>

        <section className="mb-14 border-b border-zinc-800/80 pb-10">
          <div className="mb-4">
            <h2 className="text-[11px] uppercase tracking-wider text-zinc-500 font-medium">
              Input Section
            </h2>
          </div>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Paste Slack threads, docs, meeting notes, runbooks..."
            disabled={loading}
            className="w-full h-[180px] bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-[#1D9E75]/60 resize-none transition-colors disabled:opacity-60"
          />
          <p className="mt-2 text-xs text-zinc-500">
            Paste internal docs or Slack threads to generate an executable
            workflow
          </p>

          <input
            type="text"
            value={processName}
            onChange={(e) => setProcessName(e.target.value)}
            placeholder="Process name (e.g. Customer refund handling)"
            disabled={loading}
            className="mt-3 w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-[#1D9E75]/60 transition-colors disabled:opacity-60"
          />

          <div className="flex flex-wrap items-center gap-3 mt-5">
            <span className="text-xs text-zinc-500 mr-1">Try a demo:</span>
            {DEMO_CHIPS.map((chip) => (
              <button
                key={chip.slug}
                onClick={() => loadChip(chip)}
                disabled={chipLoading !== null || loading}
                className="text-sm bg-zinc-900 hover:bg-zinc-800 hover:brightness-110 border border-zinc-800 hover:border-zinc-700 text-zinc-300 hover:text-zinc-100 px-3.5 py-2 rounded-full transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {chipLoading === chip.slug ? "Loading…" : chip.label}
              </button>
            ))}
          </div>

          <button
            onClick={generate}
            disabled={!content.trim() || !processName.trim() || loading}
            className="mt-6 w-full inline-flex items-center justify-center gap-2 bg-[#1D9E75] hover:bg-[#25b88a] disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-5 py-4 rounded-xl transition-colors shadow-lg shadow-[#1D9E75]/20"
          >
            {loading ? (
              <>
                <Spinner /> Generating workflow…
              </>
            ) : (
              "Generate workflow"
            )}
          </button>
        </section>

        {error && (
          <div className="bg-rose-950/40 border border-rose-900/60 text-rose-200 rounded-xl p-4 mb-6 text-sm">
            <strong className="font-medium">Error.</strong> {error}
          </div>
        )}

        {workflow && (
          <section
            key={workflow.generated_at ?? workflow.process}
            className="animate-fade-in"
          >
            <div className="mb-4">
              <h2 className="text-[11px] uppercase tracking-wider text-zinc-500 font-medium">
                Output Section
              </h2>
              <p className="mt-2 text-[15px] font-medium text-zinc-300">
                This workflow can be executed by an AI agent or a human.
              </p>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <WorkflowPanel workflow={workflow} />
              <SkillsFilePanel
                workflow={workflow}
                copied={copied}
                onCopy={copyJson}
              />
            </div>
          </section>
        )}

        {history.length > 0 && (
          <section className="mt-14">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium">
                Recent workflows
              </h3>
              <button
                onClick={clearHistory}
                disabled={clearingHistory}
                className={`text-xs transition-colors disabled:opacity-50 ${
                  clearConfirm
                    ? "text-amber-300 hover:text-amber-200"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {clearingHistory
                  ? "Clearing…"
                  : clearConfirm
                    ? "Click again to confirm"
                    : "Clear all"}
              </button>
            </div>
            <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
              {history.map((wf, i) => (
                <button
                  key={i}
                  onClick={() => setWorkflow(wf)}
                  className="shrink-0 text-left bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 hover:border-zinc-700 rounded-lg px-3 py-2.5 transition-colors min-w-[180px] max-w-[240px]"
                >
                  <p className="text-sm font-medium text-zinc-100 truncate">
                    {wf.process}
                  </p>
                  <p className="text-xs text-zinc-500 mt-0.5">
                    {relativeTime(wf.generated_at)}
                  </p>
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------
// Output panels
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
                      👤 {s.owner}
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
  const displayJson = formatDisplayJson(json);

  return (
    <article className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium">
            Executable workflow
          </span>
          <span className="text-[10px] uppercase tracking-wider font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 rounded-full px-2 py-0.5">
            AI-Executable
          </span>
          {workflow.confidence !== undefined && (
            <ConfidenceBadge value={workflow.confidence} />
          )}
        </div>
        <button
          onClick={onCopy}
          className="text-xs bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 px-2.5 py-1.5 rounded-md transition-colors"
        >
          {copied ? "Copied!" : "Copy JSON"}
        </button>
      </div>

      <pre
        className="font-mono text-[12.5px] leading-7 bg-zinc-950 border border-zinc-800 rounded-lg p-4 text-zinc-300 overflow-auto max-h-[520px] whitespace-pre-wrap break-words scrollbar-thin"
        dangerouslySetInnerHTML={{ __html: highlightWorkflowJson(displayJson) }}
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

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function workflowToJson(wf: Workflow): string {
  // The skill file is a clean payload — drop history-only metadata.
  const { generated_at: _gen, ...payload } = wf;
  return JSON.stringify(payload, null, 2);
}

function formatDisplayJson(json: string): string {
  return json.replace(
    /\n  "(steps|decision_rules|approvals|exceptions|sources|confidence)":/g,
    '\n\n  "$1":',
  );
}

function highlightWorkflowJson(json: string): string {
  // Escape HTML first so any `<`, `>`, `&` inside string values render safely.
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

async function copyToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!copied) throw new Error("Clipboard copy failed");
}

function ConfidenceBadge({ value }: { value: number | string }) {
  const label = String(value).toLowerCase();
  const numeric = typeof value === "number" ? value : Number.parseFloat(value);
  const normalized = numeric > 1 ? numeric / 100 : numeric;
  const tone =
    label.includes("high") ||
    (Number.isFinite(normalized) && normalized >= 0.8)
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : label.includes("medium") ||
          (Number.isFinite(normalized) && normalized >= 0.5)
        ? "bg-amber-500/15 text-amber-200 border-amber-500/30"
        : "bg-rose-500/15 text-rose-200 border-rose-500/30";

  return (
    <span
      className={`text-[10px] uppercase tracking-wider font-medium border rounded-full px-2 py-0.5 ${tone}`}
    >
      Confidence {String(value)}
    </span>
  );
}


function relativeTime(iso?: string): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "";
  const s = Math.floor(ms / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 4) return `${w}w ago`;
  const mo = Math.floor(d / 30);
  return `${mo}mo ago`;
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
