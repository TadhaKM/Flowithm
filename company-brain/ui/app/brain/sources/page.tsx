"use client";

// Connected sources dashboard. Add/remove ingest sources, see the last
// scheduled cycle's summary, manually trigger a sync. All admin-gated
// calls go through /api/admin/* server proxy routes that inject
// ADMIN_TOKEN — nothing sensitive in the browser bundle.

import Link from "next/link";
import { useEffect, useState } from "react";

type Source = {
  id: string;
  source_type: "slack" | "notion" | "github" | "gmail" | "intercom";
  display_name: string;
  config: Record<string, unknown>; // tokens redacted to "***" server-side
  last_synced_at: string | null;
  next_sync_at: string | null;
  is_active: boolean;
  created_at: string | null;
};

type IngestStatus = {
  last_run: {
    started_at: string;
    duration_seconds: number;
    sources_checked: number;
    new_chunks: number;
    skipped_chunks: number;
    new_conflicts: number;
    stale_flagged?: number;
    stale_cleared?: number;
    errors: string[];
  } | null;
  next_run_at: string | null;
  schedule_hours: number;
};

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState<IngestStatus | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  const [showAdd, setShowAdd] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [sRes, iRes] = await Promise.all([
        fetch("/api/admin/sources", { cache: "no-store" }),
        fetch("/api/admin/ingest", { cache: "no-store" }),
      ]);
      const sBody = await sRes.json();
      const iBody = await iRes.json();
      if (!sRes.ok) throw new Error(sBody?.error || `HTTP ${sRes.status}`);
      setSources(Array.isArray(sBody) ? sBody : []);
      if (iRes.ok) setStatus(iBody);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  async function syncNow() {
    if (syncing) return;
    setSyncing(true);
    try {
      const res = await fetch("/api/admin/ingest", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
      setToast("Sync started — refresh in a few seconds for the new run summary");
    } catch (e) {
      setToast(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  }

  async function deactivateSource(s: Source) {
    try {
      const res = await fetch(`/api/admin/sources/${s.id}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || `HTTP ${res.status}`);
      }
      setToast(`Removed "${s.display_name}"`);
      await loadAll();
    } catch (e) {
      setToast(`Remove failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function toggleActive(s: Source, next: boolean) {
    try {
      const res = await fetch(`/api/admin/sources/${s.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_active: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || `HTTP ${res.status}`);
      }
      await loadAll();
    } catch (e) {
      setToast(`Toggle failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <header className="mb-12 flex items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <Link href="/" className="text-base font-medium tracking-tight text-zinc-100 hover:text-zinc-300 transition-colors">
              Flowithm
            </Link>
            <Link href="/brain" className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
              Knowledge base
            </Link>
            <Link href="/brain/api" className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
              Agent API
            </Link>
            <span className="text-sm text-zinc-100 font-medium">Sources</span>
          </div>
          <p className="hidden text-sm text-zinc-500 sm:block">
            Continuous ingestion from your tools
          </p>
        </header>

        <div className="mb-8">
          <h2 className="text-2xl font-medium tracking-tight text-zinc-100">Connected sources</h2>
          <p className="mt-2 text-sm text-zinc-500">
            Flowithm checks each every <span className="text-zinc-300">{status?.schedule_hours ?? 24}h</span>.
            Add what your team writes in.
          </p>
        </div>

        <LastRunBanner
          status={status}
          showErrors={showErrors}
          onToggleErrors={() => setShowErrors((s) => !s)}
        />

        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-medium tracking-tight text-zinc-100">Sources</h3>
          <div className="flex items-center gap-2">
            <button
              onClick={syncNow}
              disabled={syncing}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50 transition-colors"
            >
              {syncing ? "Triggering…" : "Sync now"}
            </button>
            <button
              onClick={() => setShowAdd(true)}
              className="rounded-md bg-[#1D9E75] px-3.5 py-1.5 text-xs font-medium text-white hover:bg-[#178c66] transition-colors"
            >
              + Connect source
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            Couldn&apos;t load sources: {error}
          </div>
        )}

        {loading ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-8 text-center text-sm text-zinc-500">
            Loading…
          </div>
        ) : sources.length === 0 ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-8 text-center text-sm text-zinc-500">
            No sources yet — click <span className="text-zinc-300">+ Connect source</span> to add one.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {sources.map((s) => (
              <SourceCard
                key={s.id}
                source={s}
                onRemove={deactivateSource}
                onToggle={toggleActive}
              />
            ))}
          </div>
        )}
      </div>

      {showAdd && (
        <AddSourceModal
          onClose={() => setShowAdd(false)}
          onAdded={() => {
            setShowAdd(false);
            setToast("Source connected");
            loadAll();
          }}
        />
      )}

      {toast && <Toast message={toast} />}
    </main>
  );
}

// --------------------------------------------------------------------------
// Last-run banner
// --------------------------------------------------------------------------

function LastRunBanner({
  status,
  showErrors,
  onToggleErrors,
}: {
  status: IngestStatus | null;
  showErrors: boolean;
  onToggleErrors: () => void;
}) {
  if (!status) return null;
  const last = status.last_run;
  if (!last) {
    return (
      <div className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-400">
        No ingest runs yet. Add a source then click <span className="text-zinc-200">Sync now</span> — or wait
        for the next scheduled run.
      </div>
    );
  }
  const errorCount = (last.errors || []).length;
  const hasErrors = errorCount > 0;
  return (
    <div className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-zinc-300">
        <span className="text-zinc-500">Last sync:</span>
        <span>{relativeTime(last.started_at)}</span>
        <span className="text-zinc-700">·</span>
        <span><span className="text-emerald-300 tabular-nums">{last.new_chunks}</span> new chunks</span>
        <span className="text-zinc-700">·</span>
        <span className="text-zinc-500"><span className="tabular-nums text-zinc-300">{last.skipped_chunks}</span> skipped</span>
        <span className="text-zinc-700">·</span>
        <span><span className="text-amber-300 tabular-nums">{last.new_conflicts}</span> new conflicts</span>
        {last.new_conflicts > 0 && (
          <Link href="/brain" className="text-xs text-amber-300 underline-offset-4 hover:underline">
            Review →
          </Link>
        )}
        {(last.stale_flagged || 0) > 0 && (
          <>
            <span className="text-zinc-700">·</span>
            <span>
              <span className="tabular-nums text-zinc-300">{last.stale_flagged}</span> workflow
              {last.stale_flagged === 1 ? "" : "s"} need review
            </span>
          </>
        )}
        {hasErrors && (
          <>
            <span className="text-zinc-700">·</span>
            <button
              onClick={onToggleErrors}
              className="text-red-300 hover:text-red-200"
            >
              {errorCount} error{errorCount === 1 ? "" : "s"} — {showErrors ? "hide" : "view"} logs
            </button>
          </>
        )}
      </div>
      {hasErrors && showErrors && (
        <ul className="mt-3 space-y-1 rounded-md border border-red-500/20 bg-red-500/5 p-3 text-xs text-red-200">
          {last.errors.map((e, i) => (
            <li key={i} className="break-all">{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Source card
// --------------------------------------------------------------------------

function SourceCard({
  source,
  onRemove,
  onToggle,
}: {
  source: Source;
  onRemove: (s: Source) => Promise<void>;
  onToggle: (s: Source, next: boolean) => Promise<void>;
}) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!confirmRemove) return;
    const t = window.setTimeout(() => setConfirmRemove(false), 3000);
    return () => window.clearTimeout(t);
  }, [confirmRemove]);

  async function clickRemove() {
    if (!confirmRemove) {
      setConfirmRemove(true);
      return;
    }
    if (pending) return;
    setPending(true);
    try {
      await onRemove(source);
    } finally {
      setPending(false);
      setConfirmRemove(false);
    }
  }

  return (
    <article className={`rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition-opacity ${source.is_active ? "" : "opacity-60"}`}>
      <header className="mb-3 flex items-center gap-3">
        <SourceIcon type={source.source_type} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-zinc-100 truncate">{source.display_name}</div>
          <div className="text-xs text-zinc-500 capitalize">{source.source_type}</div>
        </div>
        <ActiveToggle
          active={source.is_active}
          onChange={(next) => onToggle(source, next)}
        />
      </header>

      <dl className="grid grid-cols-2 gap-y-1 text-xs text-zinc-400 mb-3">
        <dt className="text-zinc-500">Last synced</dt>
        <dd className="text-right text-zinc-300">
          {source.last_synced_at ? relativeTime(source.last_synced_at) : "Never"}
        </dd>
        <dt className="text-zinc-500">Next sync</dt>
        <dd className="text-right text-zinc-300">
          {source.next_sync_at ? relativeTime(source.next_sync_at) : "—"}
        </dd>
      </dl>

      <SourceConfigPreview type={source.source_type} config={source.config} />

      <div className="mt-3 flex justify-end">
        <button
          onClick={clickRemove}
          disabled={pending}
          className={`text-xs transition-colors disabled:opacity-50 ${
            confirmRemove ? "text-amber-300 hover:text-amber-200" : "text-zinc-400 hover:text-red-300"
          }`}
        >
          {pending ? "Removing…" : confirmRemove ? "Click again to confirm" : "Remove"}
        </button>
      </div>
    </article>
  );
}

function ActiveToggle({ active, onChange }: { active: boolean; onChange: (n: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!active)}
      title={active ? "Click to pause" : "Click to resume"}
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider transition-colors ${
        active
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
          : "border-zinc-700 bg-zinc-800 text-zinc-400"
      }`}
    >
      {active ? "Active" : "Paused"}
    </button>
  );
}

function SourceConfigPreview({
  type,
  config,
}: {
  type: Source["source_type"];
  config: Record<string, unknown>;
}) {
  const channels = (config.channel_ids as string[] | undefined) || [];
  const pages = (config.page_ids as string[] | undefined) || [];
  const tokenLabel = config.bot_token ? "bot_token: ***" : config.integration_token ? "integration_token: ***" : null;

  return (
    <div className="text-xs text-zinc-500 space-y-0.5">
      {tokenLabel && <div className="font-mono">{tokenLabel}</div>}
      {type === "slack" && channels.length > 0 && (
        <div>
          <span className="text-zinc-500">channels: </span>
          <span className="text-zinc-400">{channels.length}</span>
        </div>
      )}
      {type === "notion" && pages.length > 0 && (
        <div>
          <span className="text-zinc-500">pages: </span>
          <span className="text-zinc-400">{pages.length}</span>
        </div>
      )}
    </div>
  );
}

function SourceIcon({ type }: { type: Source["source_type"] }) {
  const colors: Record<Source["source_type"], string> = {
    slack: "bg-purple-500/15 text-purple-200 border-purple-500/30",
    notion: "bg-zinc-700/40 text-zinc-200 border-zinc-600",
    github: "bg-zinc-700/40 text-zinc-200 border-zinc-600",
    gmail: "bg-red-500/15 text-red-200 border-red-500/30",
    intercom: "bg-blue-500/15 text-blue-200 border-blue-500/30",
  };
  const initial = type.charAt(0).toUpperCase();
  return (
    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md border text-xs font-medium ${colors[type]}`}>
      {initial}
    </div>
  );
}

// --------------------------------------------------------------------------
// Add source modal — dynamic config fields per source_type
// --------------------------------------------------------------------------

function AddSourceModal({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [sourceType, setSourceType] = useState<Source["source_type"]>("slack");
  const [displayName, setDisplayName] = useState("");
  // Per-source-type fields kept in one bag — only the relevant subset is read at submit.
  const [token, setToken] = useState("");          // slack/notion/github single token; intercom access_token
  const [idsText, setIdsText] = useState("");      // slack channels / notion pages
  const [credsJson, setCredsJson] = useState("");  // gmail credentials_json
  const [labelsText, setLabelsText] = useState("");// gmail labels
  const [tagsText, setTagsText] = useState("");    // intercom tags (optional)
  const [minMsgCount, setMinMsgCount] = useState<number>(3); // intercom min_message_count
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function commaList(s: string): string[] {
    return s.split(",").map((x) => x.trim()).filter(Boolean);
  }

  function buildConfig(): { config: Record<string, unknown>; missing: string[] } {
    const missing: string[] = [];
    if (!displayName.trim()) missing.push("display name");

    if (sourceType === "slack") {
      if (!token.trim()) missing.push("bot token");
      const ids = commaList(idsText);
      if (ids.length === 0) missing.push("channel ids");
      return { config: { bot_token: token.trim(), channel_ids: ids }, missing };
    }
    if (sourceType === "notion") {
      if (!token.trim()) missing.push("integration token");
      const ids = commaList(idsText);
      if (ids.length === 0) missing.push("page ids");
      return { config: { integration_token: token.trim(), page_ids: ids }, missing };
    }
    if (sourceType === "gmail") {
      if (!credsJson.trim()) missing.push("credentials JSON");
      const labels = commaList(labelsText);
      if (labels.length === 0) missing.push("label filters");
      // Validate JSON shape early so the user sees a clear error here.
      let parsed = "";
      try {
        parsed = JSON.stringify(JSON.parse(credsJson || "{}"));
      } catch {
        missing.push("valid JSON in Credentials");
      }
      return {
        config: { credentials_json: parsed || credsJson.trim(), label_filters: labels, min_thread_length: 2 },
        missing,
      };
    }
    if (sourceType === "intercom") {
      if (!token.trim()) missing.push("access token");
      const tags = commaList(tagsText);
      const cfg: Record<string, unknown> = {
        access_token: token.trim(),
        min_message_count: Math.max(1, Number.isFinite(minMsgCount) ? minMsgCount : 3),
      };
      if (tags.length > 0) cfg.tags = tags;
      return { config: cfg, missing };
    }
    // github (no live ingestor yet — accept generic shape)
    if (!token.trim()) missing.push("token");
    const ids = commaList(idsText);
    return { config: { token: token.trim(), ids }, missing };
  }

  async function submit() {
    if (pending) return;
    const { config, missing } = buildConfig();
    if (missing.length > 0) {
      setError(`Missing: ${missing.join(", ")}`);
      return;
    }
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/sources", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source_type: sourceType, display_name: displayName.trim(), config }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <h4 className="text-base font-medium text-zinc-100">Connect source</h4>
        <p className="mt-1 text-xs text-zinc-500">
          Tokens are stored server-side and never exposed in the dashboard.
        </p>

        <label className="mt-4 block text-xs uppercase tracking-wider text-zinc-500">Source type</label>
        <select
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value as Source["source_type"])}
          className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-[#1D9E75] focus:outline-none"
        >
          <option value="slack">Slack</option>
          <option value="notion">Notion</option>
          <option value="gmail">Gmail</option>
          <option value="intercom">Intercom</option>
          <option value="github">GitHub</option>
        </select>

        <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">Display name</label>
        <input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder={
            sourceType === "slack" ? "e.g. Engineering Slack"
              : sourceType === "notion" ? "e.g. Product Wiki"
              : sourceType === "gmail" ? "e.g. Support inbox"
              : sourceType === "intercom" ? "e.g. Customer support"
              : "e.g. Source name"
          }
          className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
        />

        {/* Per-source-type fields */}
        {(sourceType === "slack" || sourceType === "notion" || sourceType === "github") && (
          <>
            <FieldLabel>{
              sourceType === "slack" ? "Bot token (xoxb-…)"
                : sourceType === "notion" ? "Integration token (secret_…)"
                : "Token"
            }</FieldLabel>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="paste token here"
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />

            <FieldLabel>{
              sourceType === "slack" ? "Channel IDs (comma-separated)"
                : sourceType === "notion" ? "Page IDs (comma-separated)"
                : "IDs (comma-separated)"
            }</FieldLabel>
            <input
              value={idsText}
              onChange={(e) => setIdsText(e.target.value)}
              placeholder={sourceType === "slack" ? "C0123ABC, C4567DEF" : "abc123…, def456…"}
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
          </>
        )}

        {sourceType === "gmail" && (
          <>
            <FieldLabel>Credentials JSON</FieldLabel>
            <textarea
              value={credsJson}
              onChange={(e) => setCredsJson(e.target.value)}
              placeholder='{"token":"…","refresh_token":"…","client_id":"…",…}'
              rows={6}
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              Run <code className="text-zinc-300">python -m ingest.gmail_auth</code> to generate
              this — see the README&apos;s Gmail section.
            </p>

            <FieldLabel>Label filters (comma-separated)</FieldLabel>
            <input
              value={labelsText}
              onChange={(e) => setLabelsText(e.target.value)}
              placeholder="process, policy, escalation, runbook"
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              Only Gmail threads with at least one of these labels are ingested.
            </p>
          </>
        )}

        {sourceType === "intercom" && (
          <>
            <FieldLabel>Access token</FieldLabel>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="paste access token here"
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              Find this in Intercom Settings → Developers → Access Tokens.
            </p>

            <FieldLabel>Tags to watch (comma-separated, optional)</FieldLabel>
            <input
              value={tagsText}
              onChange={(e) => setTagsText(e.target.value)}
              placeholder="escalated, exception, policy-question"
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              Leave blank to ingest all closed conversations.
            </p>

            <FieldLabel>Min message count</FieldLabel>
            <input
              type="number"
              min={1}
              value={minMsgCount}
              onChange={(e) => setMinMsgCount(parseInt(e.target.value, 10) || 1)}
              className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-[#1D9E75] focus:outline-none font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">
              Skip conversations shorter than this — filters out simple FAQ exchanges.
            </p>
          </>
        )}

        {error && (
          <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            {error}
          </div>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={pending}
            className="px-3.5 py-1.5 text-xs font-medium rounded-md bg-[#1D9E75] text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
          >
            {pending ? "Connecting…" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
      {children}
    </label>
  );
}

// --------------------------------------------------------------------------
// Shared bits
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
