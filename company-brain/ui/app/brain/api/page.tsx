"use client";

// Agent API dashboard: keys management, usage stats, integration snippets,
// live /skills/match playground. All admin-gated calls go through Next.js
// /api/admin/* server routes that inject ADMIN_TOKEN; nothing sensitive
// reaches the browser bundle.

import { useEffect, useMemo, useState } from "react";
import { TopNav } from "@/components/TopNav";

type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  request_count: number;
  is_active: boolean;
};

type UsageStats = {
  total_30d: number;
  avg_response_ms_30d: number;
  most_queried_process: { id: string; process: string; count: number } | null;
  daily_14d: { date: string; count: number }[];
};

type CreatedKey = { id: string; prefix: string; key: string; name: string };

export default function ApiDashboardPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [keysLoading, setKeysLoading] = useState(true);
  const [keysError, setKeysError] = useState<string | null>(null);

  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createdKey, setCreatedKey] = useState<CreatedKey | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function loadKeys() {
    setKeysLoading(true);
    setKeysError(null);
    try {
      const res = await fetch("/api/admin/keys", { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) {
        setKeysError(body?.error || `HTTP ${res.status}`);
        setKeys([]);
      } else {
        setKeys(Array.isArray(body) ? body : []);
      }
    } catch (e) {
      setKeysError(e instanceof Error ? e.message : String(e));
    } finally {
      setKeysLoading(false);
    }
  }

  async function loadUsage() {
    try {
      // Pass the browser's TZ offset so server-side bucketing matches what
      // the user reads off their wall clock, not UTC.
      const tzOffset = new Date().getTimezoneOffset();
      const res = await fetch(`/api/admin/usage?tz_offset_min=${tzOffset}`, {
        cache: "no-store",
      });
      const body = await res.json();
      if (!res.ok) setUsageError(body?.error || `HTTP ${res.status}`);
      else setUsage(body);
    } catch (e) {
      setUsageError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    loadKeys();
    loadUsage();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  async function revoke(key: ApiKey) {
    try {
      const res = await fetch(`/api/admin/keys/${key.id}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || `HTTP ${res.status}`);
      }
      setToast(`Revoked "${key.name}"`);
      await loadKeys();
    } catch (e) {
      setToast(`Revoke failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <TopNav />

        <div className="mb-8">
          <h2 className="text-2xl font-medium tracking-tight text-zinc-100">
            Agent API
          </h2>
          <p className="mt-2 text-sm text-zinc-500">
            Issue keys, watch usage, copy integration snippets, try the live{" "}
            <code className="text-zinc-300">/skills/match</code> endpoint.
          </p>
        </div>

        <KeysSection
          keys={keys}
          loading={keysLoading}
          error={keysError}
          onGenerate={() => setShowCreate(true)}
          onRevoke={revoke}
        />

        <UsageSection usage={usage} error={usageError} />

        <SnippetsSection />

        <PlaygroundSection />
      </div>

      {showCreate && (
        <CreateKeyModal
          onClose={() => {
            setShowCreate(false);
            setCreatedKey(null);
          }}
          onCreated={(key) => {
            setCreatedKey(key);
            loadKeys();
          }}
          createdKey={createdKey}
        />
      )}

      {toast && <Toast message={toast} />}
    </main>
  );
}

// --------------------------------------------------------------------------
// Keys section
// --------------------------------------------------------------------------

function KeysSection({
  keys,
  loading,
  error,
  onGenerate,
  onRevoke,
}: {
  keys: ApiKey[];
  loading: boolean;
  error: string | null;
  onGenerate: () => void;
  onRevoke: (k: ApiKey) => Promise<void>;
}) {
  return (
    <section className="mb-10">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="text-lg font-medium tracking-tight text-zinc-100">API keys</h3>
        <button
          onClick={onGenerate}
          className="rounded-md bg-[#1D9E75] px-3.5 py-1.5 text-xs font-medium text-white hover:bg-[#178c66] transition-colors"
        >
          + Generate new key
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          Couldn&apos;t load keys: {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
        <table className="w-full text-sm">
          <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
            <tr>
              <th className="px-4 py-2.5 text-left font-medium">Name</th>
              <th className="px-4 py-2.5 text-left font-medium">Prefix</th>
              <th className="px-4 py-2.5 text-left font-medium">Created</th>
              <th className="px-4 py-2.5 text-left font-medium">Last used</th>
              <th className="px-4 py-2.5 text-right font-medium">Requests</th>
              <th className="px-4 py-2.5 text-right font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/80">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-zinc-500">
                  Loading…
                </td>
              </tr>
            ) : keys.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-zinc-500">
                  No keys yet — click <span className="text-zinc-300">Generate new key</span> to issue one.
                </td>
              </tr>
            ) : (
              keys.map((k) => (
                <tr key={k.id} className={k.is_active ? "" : "opacity-50"}>
                  <td className="px-4 py-3 text-zinc-100">{k.name}</td>
                  <td className="px-4 py-3">
                    <code className="text-xs text-zinc-300">{k.prefix}…</code>
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500">{relativeTime(k.created_at)}</td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {k.last_used_at ? relativeTime(k.last_used_at) : "never"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-zinc-300">
                    {k.request_count.toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {k.is_active ? (
                      <RevokeButton apiKey={k} onRevoke={onRevoke} />
                    ) : (
                      <span className="text-xs text-zinc-600">revoked</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RevokeButton({
  apiKey,
  onRevoke,
}: {
  apiKey: ApiKey;
  onRevoke: (k: ApiKey) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  // Auto-cancel the confirm state after 3s of inactivity — same idle reset
  // as the Archive kebab uses so misclicks don't sit primed indefinitely.
  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(false), 3000);
    return () => window.clearTimeout(t);
  }, [confirming]);

  async function click() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    if (pending) return;
    setPending(true);
    try {
      await onRevoke(apiKey);
    } finally {
      setPending(false);
      setConfirming(false);
    }
  }

  return (
    <button
      onClick={click}
      disabled={pending}
      className={`text-xs transition-colors disabled:opacity-50 ${
        confirming
          ? "text-amber-300 hover:text-amber-200"
          : "text-zinc-400 hover:text-red-300"
      }`}
    >
      {pending ? "Revoking…" : confirming ? "Click again to confirm" : "Revoke"}
    </button>
  );
}

function CreateKeyModal({
  onClose,
  onCreated,
  createdKey,
}: {
  onClose: () => void;
  onCreated: (key: CreatedKey) => void;
  createdKey: CreatedKey | null;
}) {
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function submit() {
    if (!name.trim() || pending) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/keys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
      onCreated(body as CreatedKey);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  async function copyKey() {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — user can select manually */
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {!createdKey ? (
          <>
            <h4 className="text-base font-medium text-zinc-100">Generate API key</h4>
            <p className="mt-1 text-sm text-zinc-500">
              Pick a label that tells you where this key is used.
            </p>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              placeholder="e.g. Production support bot"
              className="mt-4 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
              autoFocus
            />
            {error && (
              <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                {error}
              </div>
            )}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                onClick={onClose}
                className="px-3.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={!name.trim() || pending}
                className="px-3.5 py-1.5 text-xs font-medium rounded-md bg-[#1D9E75] text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
              >
                {pending ? "Generating…" : "Generate"}
              </button>
            </div>
          </>
        ) : (
          <>
            <h4 className="text-base font-medium text-zinc-100">Key created</h4>
            <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              This key will not be shown again. Copy it now and store it somewhere safe.
            </div>
            <div className="mt-3 flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-900 p-3">
              <code className="flex-1 select-all break-all text-xs text-zinc-100">
                {createdKey.key}
              </code>
              <button
                onClick={copyKey}
                className="shrink-0 rounded-md border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-700 transition-colors"
              >
                {copied ? "Copied ✓" : "Copy"}
              </button>
            </div>
            <p className="mt-3 text-xs text-zinc-500">
              Name: <span className="text-zinc-300">{createdKey.name}</span>{" "}
              · Prefix: <code className="text-zinc-300">{createdKey.prefix}</code>
            </p>
            <div className="mt-5 flex items-center justify-end">
              <button
                onClick={onClose}
                className="px-3.5 py-1.5 text-xs font-medium rounded-md bg-[#1D9E75] text-white hover:bg-[#178c66] transition-colors"
              >
                Done
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Usage section
// --------------------------------------------------------------------------

function UsageSection({
  usage,
  error,
}: {
  usage: UsageStats | null;
  error: string | null;
}) {
  return (
    <section className="mb-10">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="text-lg font-medium tracking-tight text-zinc-100">
          Usage <span className="text-zinc-500 text-xs ml-2">last 30 days</span>
        </h3>
      </div>
      {error && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          Couldn&apos;t load usage: {error}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 mb-4">
        <UsageCard label="Total API requests" value={usage ? usage.total_30d.toLocaleString() : "—"} />
        <UsageCard
          label="Most queried process"
          value={usage?.most_queried_process?.process || "—"}
          sub={usage?.most_queried_process ? `${usage.most_queried_process.count.toLocaleString()} requests` : undefined}
        />
        <UsageCard
          label="Avg response time"
          value={usage ? `${usage.avg_response_ms_30d}ms` : "—"}
        />
      </div>
      <DailyChart days={usage?.daily_14d || []} />
    </section>
  );
}

function UsageCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="text-xs uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-medium tracking-tight text-zinc-100 truncate">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function DailyChart({ days }: { days: { date: string; count: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.count));
  const W = 800;
  const H = 140;
  const padX = 12;
  const padY = 16;
  const innerW = W - padX * 2;
  const innerH = H - padY * 2;
  const barWidth = innerW / Math.max(days.length, 1) - 4;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wider text-zinc-500">Requests / day</span>
        <span className="text-xs text-zinc-500">last {days.length} days</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        {days.map((d, i) => {
          const h = (d.count / max) * innerH;
          const x = padX + i * (innerW / days.length) + 2;
          const y = padY + (innerH - h);
          return (
            <g key={d.date}>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={h || 1}
                rx={2}
                className="fill-[#1D9E75]"
                opacity={d.count === 0 ? 0.15 : 0.85}
              >
                <title>
                  {d.date}: {d.count} request{d.count === 1 ? "" : "s"}
                </title>
              </rect>
            </g>
          );
        })}
        <line
          x1={padX}
          y1={padY + innerH}
          x2={W - padX}
          y2={padY + innerH}
          className="stroke-zinc-800"
          strokeWidth={1}
        />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-zinc-600 px-3">
        <span>{days[0]?.date || ""}</span>
        <span>{days[days.length - 1]?.date || ""}</span>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Snippets section
// --------------------------------------------------------------------------

const SNIPPETS = {
  ts: `// Get the right workflow for any situation
const res = await fetch(
  'https://flowithm.io/api/v1/skills/match?q=' +
    encodeURIComponent(userQuery),
  { headers: { Authorization: 'Bearer YOUR_API_KEY' } }
);
const { skill } = await res.json();

// Always check needs_review before auto-executing
if (skill.needs_review) {
  return escalateToHuman(skill.process, skill.needs_review_reason);
}

// skill.steps          — the exact workflow to execute
// skill.decision_rules — the if/then logic
// skill.approvals      — when to escalate`,

  py: `import requests

def get_workflow(situation: str) -> dict:
    response = requests.get(
        "https://flowithm.io/api/v1/skills/match",
        params={"q": situation},
        headers={"Authorization": f"Bearer {FLOWITHM_API_KEY}"},
    )
    return response.json()["skill"]

# Use in your agent
workflow = get_workflow("customer requesting refund after 45 days")

# Always check needs_review before auto-executing
if workflow.get("needs_review"):
    return escalate_to_human(workflow["process"], workflow.get("needs_review_reason"))

for step in workflow["steps"]:
    execute_step(step)`,

  claude: `# Define Flowithm as a tool for your Claude agent.
# Once Claude calls it, inspect the response — if needs_review is true,
# stop and escalate rather than acting on the workflow.
tools = [{
    "name": "get_company_workflow",
    "description": (
        "Retrieves the exact workflow your company uses for a given "
        "situation. Call this before taking any action that involves "
        "a company process. Check the returned skill.needs_review flag "
        "before executing — if true, escalate to a human."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "situation": {
                "type": "string",
                "description": (
                    "Natural-language description of the situation "
                    "you need a workflow for"
                ),
            }
        },
        "required": ["situation"],
    },
}]`,
};

function SnippetsSection() {
  const [tab, setTab] = useState<"ts" | "py" | "claude">("ts");
  return (
    <section className="mb-10">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="text-lg font-medium tracking-tight text-zinc-100">Integrate</h3>
      </div>
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 overflow-hidden">
        <div className="flex items-center gap-1 border-b border-zinc-800 bg-zinc-900/60 px-2 py-1.5">
          <SnippetTab label="TypeScript" active={tab === "ts"} onClick={() => setTab("ts")} />
          <SnippetTab label="Python" active={tab === "py"} onClick={() => setTab("py")} />
          <SnippetTab label="Claude tool use" active={tab === "claude"} onClick={() => setTab("claude")} />
        </div>
        <pre className="p-4 text-xs leading-relaxed text-zinc-200 overflow-x-auto whitespace-pre">
          {SNIPPETS[tab]}
        </pre>
      </div>
    </section>
  );
}

function SnippetTab({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
        active
          ? "bg-zinc-800 text-zinc-100"
          : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60"
      }`}
    >
      {label}
    </button>
  );
}

// --------------------------------------------------------------------------
// Live API playground
// --------------------------------------------------------------------------

function PlaygroundSection() {
  const [q, setQ] = useState("");
  const [pending, setPending] = useState(false);
  const [response, setResponse] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  async function tryIt() {
    if (!q.trim() || pending) return;
    setPending(true);
    setResponse(null);
    setElapsed(null);
    setStatus(null);
    const started = performance.now();
    try {
      const res = await fetch(`/api/admin/playground?q=${encodeURIComponent(q.trim())}`);
      setStatus(res.status);
      const headerElapsed = res.headers.get("x-flowithm-elapsed-ms");
      setElapsed(headerElapsed ? parseInt(headerElapsed, 10) : Math.round(performance.now() - started));
      const text = await res.text();
      try {
        setResponse(JSON.stringify(JSON.parse(text), null, 2));
      } catch {
        setResponse(text);
      }
    } catch (e) {
      setResponse(JSON.stringify({ error: e instanceof Error ? e.message : String(e) }, null, 2));
      setStatus(0);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mb-10">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="text-lg font-medium tracking-tight text-zinc-100">Try it live</h3>
      </div>
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <p className="mb-3 text-xs text-zinc-500">
          Calls{" "}
          <code className="text-zinc-300">GET /api/v1/skills/match</code> using a server-side
          playground key. Real keys are never sent to the browser.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") tryIt();
            }}
            placeholder="A customer wants a refund after 45 days"
            className="flex-1 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />
          <button
            onClick={tryIt}
            disabled={!q.trim() || pending}
            className="rounded-md bg-[#1D9E75] px-4 py-2 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
          >
            {pending ? "Calling…" : "Try it"}
          </button>
        </div>

        {response !== null && (
          <div className="mt-4">
            <div className="mb-2 flex items-center gap-3 text-xs text-zinc-500">
              <span>
                Status:{" "}
                <span className={status && status >= 200 && status < 300 ? "text-emerald-300" : "text-red-300"}>
                  {status ?? "—"}
                </span>
              </span>
              {elapsed !== null && <span>Time: <span className="text-zinc-300">{elapsed}ms</span></span>}
            </div>
            <pre className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-xs leading-relaxed overflow-x-auto whitespace-pre-wrap break-all">
              <SyntaxHighlightedJson text={response} />
            </pre>
          </div>
        )}
      </div>
    </section>
  );
}

// Tiny regex-based JSON highlighter — keys teal, strings zinc, numbers/bools amber.
// Escapes HTML before tokenising so user-controlled response can't inject markup.
function SyntaxHighlightedJson({ text }: { text: string }) {
  const html = useMemo(() => highlight(text), [text]);
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlight(json: string): string {
  const safe = escapeHtml(json);
  return safe.replace(
    /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (_, key, str, kw, num) => {
      if (key) return `<span style="color:#1D9E75">${key}</span>`;
      if (str) return `<span style="color:#a1a1aa">${str}</span>`;
      if (kw) return `<span style="color:#f59e0b">${kw}</span>`;
      if (num) return `<span style="color:#f59e0b">${num}</span>`;
      return "";
    },
  );
}

// --------------------------------------------------------------------------
// Shared helpers
// --------------------------------------------------------------------------

function Toast({ message }: { message: string }) {
  return (
    <div className="fixed bottom-6 right-6 z-50 rounded-lg border border-zinc-700 bg-zinc-900/95 px-4 py-2.5 text-sm text-zinc-100 shadow-lg backdrop-blur">
      {message}
    </div>
  );
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return iso;
  const diff = Date.now() - ts;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mo = Math.round(day / 30);
  return `${mo}mo ago`;
}
