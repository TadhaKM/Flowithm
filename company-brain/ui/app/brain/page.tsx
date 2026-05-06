"use client";

// Knowledge base dashboard. Lists every non-archived workflow with metrics,
// search, filter, sort, and grid/list views. Conflict detection is gated
// on `conflictCount > 0` — wire up actual detection (e.g. via the
// find_similar_workflow RPC) when we want the banner to show.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type WorkflowStep = {
  step: number;
  action: string;
  owner: string;
  notes: string;
};

type Workflow = {
  id: string;
  process: string;
  trigger: string;
  steps: WorkflowStep[];
  decision_rules: string[];
  approvals: string[];
  exceptions: string[];
  sources: string[];
  source: string;
  source_metadata: Record<string, unknown>;
  generated_at?: string | null;
  reviewed_at?: string | null;
};

type SourceFilter = "all" | "slack" | "notion" | "manual" | "github";
type SortOption = "newest" | "oldest" | "az" | "most_steps";
type ViewMode = "grid" | "list";

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All sources" },
  { value: "slack", label: "Slack" },
  { value: "notion", label: "Notion" },
  { value: "manual", label: "Manual" },
  { value: "github", label: "GitHub" },
];

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "az", label: "A-Z" },
  { value: "most_steps", label: "Most steps" },
];

export default function BrainPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, 150);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [sort, setSort] = useState<SortOption>("newest");
  const [view, setView] = useState<ViewMode>("grid");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/brain")
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        return res.json();
      })
      .then((data: { workflows: Workflow[] }) => {
        if (!cancelled) {
          setWorkflows(data.workflows || []);
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
  }, []);

  // Conflict count: not detected yet. When we wire pg_trgm-based detection
  // through (e.g. /api/brain/conflicts using find_similar_workflow), set
  // this from state. For now the banner stays hidden.
  const conflictCount = 0;

  const filtered = useMemo(() => {
    let result = workflows;
    if (sourceFilter !== "all") {
      result = result.filter((w) => w.source === sourceFilter);
    }
    if (debouncedSearch.trim()) {
      const q = debouncedSearch.toLowerCase();
      result = result.filter(
        (w) =>
          w.process.toLowerCase().includes(q) ||
          w.trigger.toLowerCase().includes(q),
      );
    }
    const sorted = [...result];
    if (sort === "newest") {
      sorted.sort((a, b) =>
        (b.generated_at || "").localeCompare(a.generated_at || ""),
      );
    } else if (sort === "oldest") {
      sorted.sort((a, b) =>
        (a.generated_at || "").localeCompare(b.generated_at || ""),
      );
    } else if (sort === "az") {
      sorted.sort((a, b) => a.process.localeCompare(b.process));
    } else if (sort === "most_steps") {
      sorted.sort((a, b) => (b.steps?.length || 0) - (a.steps?.length || 0));
    }
    return sorted;
  }, [workflows, sourceFilter, debouncedSearch, sort]);

  function removeFromList(id: string) {
    setWorkflows((current) => current.filter((w) => w.id !== id));
  }

  return (
    <main className="min-h-screen">
      <div className="max-w-7xl mx-auto px-6 py-8">
        <BrainHeader />

        <div className="mb-8">
          <h2 className="text-2xl font-medium tracking-tight text-zinc-100">
            Knowledge base
          </h2>
          <p className="mt-2 text-sm text-zinc-500">
            Every workflow Flowithm has captured. Search, filter, and review.
          </p>
        </div>

        <MetricsRow workflows={workflows} loading={loading} />

        {conflictCount > 0 && <ConflictBanner count={conflictCount} />}

        {!loading && workflows.length > 0 && (
          <SearchFilterBar
            search={search}
            onSearch={setSearch}
            sourceFilter={sourceFilter}
            onSource={setSourceFilter}
            sort={sort}
            onSort={setSort}
            view={view}
            onView={setView}
            count={filtered.length}
          />
        )}

        {error && !loading && <ErrorBanner message={error} />}

        {loading ? (
          <GridSkeleton />
        ) : workflows.length === 0 ? (
          <EmptyState />
        ) : filtered.length === 0 ? (
          <NoResults
            onClear={() => {
              setSearch("");
              setSourceFilter("all");
            }}
          />
        ) : view === "grid" ? (
          <WorkflowsGrid workflows={filtered} onArchived={removeFromList} />
        ) : (
          <WorkflowsList workflows={filtered} onArchived={removeFromList} />
        )}
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------
// Header
// --------------------------------------------------------------------------

function BrainHeader() {
  return (
    <header className="mb-12 flex items-center justify-between gap-4">
      <div className="flex items-center gap-6">
        <Link
          href="/"
          className="text-base font-medium tracking-tight text-zinc-100 hover:text-zinc-300 transition-colors"
        >
          Flowithm
        </Link>
        <span className="text-sm text-zinc-100 font-medium">
          Knowledge base
        </span>
      </div>
      <p className="text-sm text-zinc-500 hidden sm:block">
        Every workflow your team has captured
      </p>
    </header>
  );
}

// --------------------------------------------------------------------------
// Metrics row
// --------------------------------------------------------------------------

function MetricsRow({
  workflows,
  loading,
}: {
  workflows: Workflow[];
  loading: boolean;
}) {
  const total = workflows.length;
  const sources = useMemo(() => {
    const s = new Set<string>();
    for (const w of workflows) if (w.source) s.add(w.source);
    return Array.from(s);
  }, [workflows]);
  const lastUpdated = useMemo(() => {
    let latest: Workflow | null = null;
    for (const w of workflows) {
      if (!latest || (w.generated_at || "") > (latest.generated_at || "")) {
        latest = w;
      }
    }
    return latest;
  }, [workflows]);
  const coverage = useMemo(() => {
    if (workflows.length === 0) return 0;
    const ok = workflows.filter(
      (w) => (w.steps?.length || 0) >= 3 && (w.decision_rules?.length || 0) >= 1,
    ).length;
    return Math.round((ok / workflows.length) * 100);
  }, [workflows]);

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <MetricCard label="Workflows mapped" icon={<GridIcon />} loading={loading}>
        <CountUp value={total} />
      </MetricCard>

      <MetricCard
        label="Sources connected"
        icon={<PlugIcon />}
        loading={loading}
      >
        <CountUp value={sources.length} />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sources.map((s) => (
            <span
              key={s}
              className="text-[10px] uppercase tracking-wider bg-zinc-800/80 border border-zinc-700/60 text-zinc-300 rounded-full px-2 py-0.5"
            >
              {s}
            </span>
          ))}
        </div>
      </MetricCard>

      <MetricCard
        label="Most recently updated"
        icon={<ClockIcon />}
        loading={loading}
      >
        <span className="text-base font-medium text-zinc-100 line-clamp-1">
          {lastUpdated?.process || "—"}
        </span>
        <p className="text-xs text-zinc-500 mt-1">
          {lastUpdated?.generated_at
            ? relativeTime(lastUpdated.generated_at)
            : ""}
        </p>
      </MetricCard>

      <MetricCard
        label="Workflow quality"
        icon={<CheckCircleIcon />}
        loading={loading}
        tooltip="Workflows with full decision logic (3+ steps and 1+ decision rule)"
      >
        <CountUp value={coverage} suffix="%" />
        <div className="mt-3 h-1 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-[#1D9E75] transition-all duration-700 ease-out"
            style={{ width: `${coverage}%` }}
          />
        </div>
      </MetricCard>
    </div>
  );
}

function MetricCard({
  label,
  icon,
  loading,
  tooltip,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  loading: boolean;
  tooltip?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 transition-colors hover:border-zinc-700"
      title={tooltip}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] uppercase tracking-wider text-zinc-500 font-medium">
          {label}
        </span>
        <span className="text-zinc-500">{icon}</span>
      </div>
      <div
        className={`text-[28px] font-medium text-zinc-100 leading-none ${
          loading ? "opacity-30" : ""
        }`}
      >
        {loading ? "—" : children}
      </div>
    </div>
  );
}

function CountUp({ value, suffix = "" }: { value: number; suffix?: string }) {
  const display = useCountUp(value, 600);
  return (
    <span>
      {display}
      {suffix}
    </span>
  );
}

// --------------------------------------------------------------------------
// Conflict banner (placeholder — wired to render when count > 0)
// --------------------------------------------------------------------------

function ConflictBanner({ count }: { count: number }) {
  return (
    <div className="mb-8 flex items-center justify-between gap-4 bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-3 text-sm text-amber-100">
      <div className="flex items-center gap-3">
        <WarningIcon />
        <span>
          <strong className="font-medium">{count} process conflicts detected</strong>{" "}
          — your knowledge base may be out of date
        </span>
      </div>
      <button
        onClick={() => {
          const el = document.getElementById("conflicts-section");
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
        className="text-xs text-amber-200 hover:text-amber-100 underline-offset-4 hover:underline transition-colors shrink-0"
      >
        Review conflicts →
      </button>
    </div>
  );
}

// --------------------------------------------------------------------------
// Search + filter bar
// --------------------------------------------------------------------------

function SearchFilterBar({
  search,
  onSearch,
  sourceFilter,
  onSource,
  sort,
  onSort,
  view,
  onView,
  count,
}: {
  search: string;
  onSearch: (s: string) => void;
  sourceFilter: SourceFilter;
  onSource: (s: SourceFilter) => void;
  sort: SortOption;
  onSort: (s: SortOption) => void;
  view: ViewMode;
  onView: (v: ViewMode) => void;
  count: number;
}) {
  return (
    <div className="mb-6 flex flex-col sm:flex-row sm:items-center gap-3">
      <div className="relative flex-1 min-w-0">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none">
          <SearchIcon />
        </span>
        <input
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search workflows…"
          className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-9 pr-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-[#1D9E75]/60 transition-colors"
        />
      </div>

      <select
        value={sourceFilter}
        onChange={(e) => onSource(e.target.value as SourceFilter)}
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100 hover:border-zinc-700 focus:outline-none focus:border-[#1D9E75]/60 transition-colors"
      >
        {SOURCE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      <select
        value={sort}
        onChange={(e) => onSort(e.target.value as SortOption)}
        className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100 hover:border-zinc-700 focus:outline-none focus:border-[#1D9E75]/60 transition-colors"
      >
        {SORT_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      <div className="flex items-center gap-1 bg-zinc-900 border border-zinc-800 rounded-lg p-1">
        <button
          onClick={() => onView("grid")}
          aria-label="Grid view"
          className={`p-1.5 rounded transition-colors ${
            view === "grid"
              ? "bg-zinc-800 text-zinc-100"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          <GridSmallIcon />
        </button>
        <button
          onClick={() => onView("list")}
          aria-label="List view"
          className={`p-1.5 rounded transition-colors ${
            view === "list"
              ? "bg-zinc-800 text-zinc-100"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          <ListIcon />
        </button>
      </div>

      <span className="text-xs text-zinc-500 sm:ml-2 shrink-0">
        {count} workflow{count === 1 ? "" : "s"}
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------
// Grid + list views
// --------------------------------------------------------------------------

function WorkflowsGrid({
  workflows,
  onArchived,
}: {
  workflows: Workflow[];
  onArchived: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {workflows.map((w, i) => (
        <WorkflowCard
          key={w.id}
          workflow={w}
          index={i}
          onArchived={onArchived}
        />
      ))}
    </div>
  );
}

function WorkflowCard({
  workflow,
  index,
  onArchived,
}: {
  workflow: Workflow;
  index: number;
  onArchived: (id: string) => void;
}) {
  const router = useRouter();
  const sourceMeta = workflow.source_metadata || {};
  const sourceLabel =
    workflow.source === "slack"
      ? `#${(sourceMeta.channel_name as string) || "slack"}`
      : workflow.source === "notion"
        ? (sourceMeta.page_title as string) || "Notion"
        : workflow.source === "github"
          ? "GitHub"
          : "Manual";

  return (
    <div
      className="group bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl p-5 cursor-pointer transition-colors animate-fade-in [animation-fill-mode:both]"
      style={{ animationDelay: `${index * 30}ms` }}
      onClick={() => router.push(`/brain/${workflow.id}`)}
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <SourceBadge type={workflow.source} />
          <span className="text-xs text-zinc-400 truncate">{sourceLabel}</span>
        </div>
        <span className="text-xs text-zinc-500 shrink-0">
          {relativeTime(workflow.generated_at)}
        </span>
      </div>

      <h3 className="text-base font-medium text-zinc-100 leading-snug line-clamp-2">
        {workflow.process}
      </h3>
      {workflow.trigger && (
        <p className="mt-1 text-[13px] text-zinc-500 italic line-clamp-1">
          {workflow.trigger}
        </p>
      )}

      {workflow.steps.length > 0 && (
        <div className="mt-3 space-y-1 text-[12px] text-zinc-500">
          {workflow.steps.slice(0, 2).map((s) => (
            <p key={s.step} className="line-clamp-1">
              {s.step}. {s.action}
              {s.owner && s.owner !== "unspecified" && (
                <span className="text-zinc-600">  •  {s.owner}</span>
              )}
            </p>
          ))}
          {workflow.steps.length > 2 && (
            <p className="text-zinc-600">
              + {workflow.steps.length - 2} more steps
            </p>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1.5">
        {workflow.decision_rules.length > 0 && (
          <Pill tone="teal">
            {workflow.decision_rules.length} rule
            {workflow.decision_rules.length === 1 ? "" : "s"}
          </Pill>
        )}
        {workflow.approvals.length > 0 && (
          <Pill tone="amber">
            {workflow.approvals.length} approval
            {workflow.approvals.length === 1 ? "" : "s"}
          </Pill>
        )}
        {workflow.exceptions.length > 0 && (
          <Pill tone="gray">
            {workflow.exceptions.length} exception
            {workflow.exceptions.length === 1 ? "" : "s"}
          </Pill>
        )}
      </div>

      <div className="mt-4 pt-3 border-t border-zinc-800 flex items-center justify-between">
        <Link
          href={`/brain/${workflow.id}`}
          onClick={(e) => e.stopPropagation()}
          className="text-xs font-medium text-zinc-200 hover:text-white transition-colors"
        >
          View workflow →
        </Link>
        <KebabMenu workflow={workflow} onArchived={onArchived} />
      </div>
    </div>
  );
}

function WorkflowsList({
  workflows,
  onArchived,
}: {
  workflows: Workflow[];
  onArchived: (id: string) => void;
}) {
  const router = useRouter();
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-zinc-950/40">
          <tr className="text-[10px] uppercase tracking-wider text-zinc-500">
            <th className="text-left font-medium px-3 py-2.5 w-10"></th>
            <th className="text-left font-medium px-3 py-2.5">Process</th>
            <th className="text-left font-medium px-3 py-2.5 hidden md:table-cell">
              Trigger
            </th>
            <th className="text-left font-medium px-3 py-2.5 w-20">Steps</th>
            <th className="text-left font-medium px-3 py-2.5 w-20">Rules</th>
            <th className="text-left font-medium px-3 py-2.5 w-32 hidden sm:table-cell">
              Updated
            </th>
            <th className="text-right font-medium px-3 py-2.5 w-32"></th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w, i) => (
            <tr
              key={w.id}
              className="border-t border-zinc-800 hover:bg-zinc-950/40 transition-colors cursor-pointer animate-fade-in [animation-fill-mode:both]"
              style={{ animationDelay: `${Math.min(i, 50) * 15}ms` }}
              onClick={() => router.push(`/brain/${w.id}`)}
            >
              <td className="px-3 py-3">
                <SourceBadge type={w.source} />
              </td>
              <td className="px-3 py-3 text-zinc-100 font-medium truncate max-w-xs">
                {w.process}
              </td>
              <td className="px-3 py-3 text-zinc-500 italic truncate max-w-xs hidden md:table-cell">
                {w.trigger}
              </td>
              <td className="px-3 py-3 text-zinc-400">{w.steps.length}</td>
              <td className="px-3 py-3 text-zinc-400">
                {w.decision_rules.length}
              </td>
              <td className="px-3 py-3 text-zinc-500 hidden sm:table-cell">
                {relativeTime(w.generated_at)}
              </td>
              <td
                className="px-3 py-3 text-right"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-end gap-2">
                  <Link
                    href={`/brain/${w.id}`}
                    className="text-xs text-zinc-200 hover:text-white"
                  >
                    View
                  </Link>
                  <KebabMenu workflow={w} onArchived={onArchived} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
// Pills + badges
// --------------------------------------------------------------------------

function Pill({
  tone,
  children,
}: {
  tone: "teal" | "amber" | "gray";
  children: React.ReactNode;
}) {
  const styles = {
    teal: "bg-[#1D9E75]/15 text-emerald-300 border-[#1D9E75]/30",
    amber: "bg-amber-500/10 text-amber-200 border-amber-500/30",
    gray: "bg-zinc-800 text-zinc-400 border-zinc-700/60",
  } as const;
  return (
    <span
      className={`text-[11px] font-medium border rounded-full px-2 py-0.5 ${styles[tone]}`}
    >
      {children}
    </span>
  );
}

function SourceBadge({ type }: { type: string }) {
  const styles: Record<string, { bg: string; text: string }> = {
    slack: { bg: "bg-[#4A154B]/20 border-[#4A154B]/40", text: "text-purple-200" },
    notion: { bg: "bg-zinc-800 border-zinc-700", text: "text-zinc-100" },
    github: { bg: "bg-zinc-800 border-zinc-700", text: "text-zinc-200" },
    manual: { bg: "bg-zinc-800 border-zinc-700", text: "text-zinc-300" },
  };
  const s = styles[type] || styles.manual;
  return (
    <span
      className={`shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full border ${s.bg}`}
    >
      <span className={`w-3 h-3 ${s.text}`}>
        {type === "slack" ? <SlackMark /> : type === "notion" ? <NotionMark /> : type === "github" ? <GitHubMark /> : <UploadMark />}
      </span>
    </span>
  );
}

// --------------------------------------------------------------------------
// Kebab menu (Copy JSON, Archive)
// --------------------------------------------------------------------------

function KebabMenu({
  workflow,
  onArchived,
}: {
  workflow: Workflow;
  onArchived: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setConfirmArchive(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  async function copyJson() {
    const { id, ...rest } = workflow;
    void id;
    try {
      await navigator.clipboard.writeText(JSON.stringify(rest, null, 2));
    } catch {
      // ignore
    }
    setOpen(false);
  }

  async function archive() {
    if (!confirmArchive) {
      setConfirmArchive(true);
      return;
    }
    setArchiving(true);
    try {
      const res = await fetch(`/api/brain/${workflow.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onArchived(workflow.id);
    } catch (e) {
      console.error("archive failed:", e);
    } finally {
      setArchiving(false);
      setOpen(false);
      setConfirmArchive(false);
    }
  }

  return (
    <div className="relative" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        aria-label="More actions"
        className="text-zinc-500 hover:text-zinc-200 transition-colors p-1 rounded hover:bg-zinc-800"
      >
        <KebabIcon />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-44 bg-zinc-900 border border-zinc-800 rounded-lg shadow-xl shadow-black/40 overflow-hidden z-10">
          <button
            onClick={copyJson}
            className="w-full text-left text-xs text-zinc-200 hover:bg-zinc-800 transition-colors px-3 py-2"
          >
            Copy JSON
          </button>
          <button
            onClick={archive}
            disabled={archiving}
            className={`w-full text-left text-xs transition-colors px-3 py-2 ${
              confirmArchive
                ? "text-amber-300 hover:bg-amber-500/10"
                : "text-zinc-200 hover:bg-zinc-800"
            }`}
          >
            {archiving
              ? "Archiving…"
              : confirmArchive
                ? "Click again to confirm"
                : "Archive"}
          </button>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Empty / loading / error states
// --------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="text-center py-16">
      <div className="inline-flex items-center justify-center w-32 h-32 mb-6 text-zinc-700">
        <BrainNetwork />
      </div>
      <h3 className="text-lg font-medium text-zinc-100">
        Your knowledge base is empty
      </h3>
      <p className="mt-2 text-sm text-zinc-500 max-w-md mx-auto">
        Generate your first workflow from the home page, or invite the Slack
        bot to a channel.
      </p>
      <div className="mt-6 flex items-center justify-center gap-3">
        <Link
          href="/"
          className="inline-flex items-center gap-2 bg-[#1D9E75] hover:bg-[#22b384] text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          Generate a workflow →
        </Link>
        <a
          href="https://api.slack.com/apps"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-200 text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          Set up Slack bot →
        </a>
      </div>
    </div>
  );
}

function NoResults({ onClear }: { onClear: () => void }) {
  return (
    <div className="text-center py-16">
      <p className="text-sm text-zinc-500">No workflows match your filters.</p>
      <button
        onClick={onClear}
        className="mt-3 text-sm text-zinc-300 hover:text-white underline-offset-4 hover:underline transition-colors"
      >
        Clear filters
      </button>
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 animate-pulse"
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-full bg-zinc-800" />
              <div className="h-3 w-20 bg-zinc-800 rounded" />
            </div>
            <div className="h-3 w-12 bg-zinc-800 rounded" />
          </div>
          <div className="h-5 w-3/4 bg-zinc-800 rounded mb-2" />
          <div className="h-3 w-1/2 bg-zinc-800 rounded mb-4" />
          <div className="space-y-2">
            <div className="h-3 w-full bg-zinc-800 rounded" />
            <div className="h-3 w-5/6 bg-zinc-800 rounded" />
          </div>
          <div className="mt-4 flex gap-2">
            <div className="h-5 w-16 bg-zinc-800 rounded-full" />
            <div className="h-5 w-20 bg-zinc-800 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="bg-rose-950/40 border border-rose-900/60 text-rose-200 rounded-xl p-4 mb-6 text-sm">
      <strong className="font-medium">Couldn't load workflows.</strong>{" "}
      {message}
    </div>
  );
}

// --------------------------------------------------------------------------
// Hooks
// --------------------------------------------------------------------------

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

function useCountUp(target: number, duration: number) {
  const [value, setValue] = useState(0);
  const targetRef = useRef(target);

  useEffect(() => {
    const start = performance.now();
    const startValue = 0;
    targetRef.current = target;
    let raf = 0;

    function tick(now: number) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(startValue + (targetRef.current - startValue) * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    }

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

function relativeTime(iso?: string | null): string {
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

// --------------------------------------------------------------------------
// Icons
// --------------------------------------------------------------------------

function GridIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </svg>
  );
}

function PlugIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 2v6M15 2v6M5 8h14v3a7 7 0 0 1-14 0z" />
      <path d="M12 18v4" />
    </svg>
  );
}

function ClockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

function CheckCircleIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.1V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <circle cx="12" cy="17" r="0.5" fill="currentColor" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function GridSmallIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  );
}

function KebabIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="5" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="12" cy="19" r="1.5" />
    </svg>
  );
}

function SlackMark() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-full h-full">
      <path d="M9 2a2 2 0 1 0 0 4h2V4a2 2 0 0 0-2-2zm0 6a2 2 0 0 0-2 2v6a2 2 0 0 0 4 0v-6a2 2 0 0 0-2-2zm6-6a2 2 0 0 0-2 2v2h2a2 2 0 0 0 0-4zm0 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm-9 6a2 2 0 0 0 0 4 2 2 0 0 0 2-2v-2zm12-6a2 2 0 1 0 0-4h-2v2a2 2 0 0 0 2 2zm-6 8a2 2 0 1 0 4 0v-6a2 2 0 1 0-4 0zM6 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" />
    </svg>
  );
}

function NotionMark() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-full h-full">
      <path d="M5 4h14v16H5z" opacity="0" />
      <path d="M7 5v14l2-1V8.2l5 8.5 3-1V5l-2 1v9.5L10.5 7z" />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="w-full h-full">
      <path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11 11 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.7 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 6 .4.4.8 1.1.8 2.2v3.2c0 .3.2.7.8.6A12 12 0 0 0 12 .3" />
    </svg>
  );
}

function UploadMark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function BrainNetwork() {
  return (
    <svg viewBox="0 0 200 160" fill="none" className="w-full h-full">
      <line x1="100" y1="80" x2="40" y2="40" stroke="currentColor" strokeOpacity="0.4" />
      <line x1="100" y1="80" x2="160" y2="40" stroke="currentColor" strokeOpacity="0.4" />
      <line x1="100" y1="80" x2="40" y2="120" stroke="currentColor" strokeOpacity="0.4" />
      <line x1="100" y1="80" x2="160" y2="120" stroke="currentColor" strokeOpacity="0.4" />
      <line x1="100" y1="80" x2="100" y2="20" stroke="currentColor" strokeOpacity="0.3" />
      <line x1="100" y1="80" x2="100" y2="140" stroke="currentColor" strokeOpacity="0.3" />
      <line x1="40" y1="40" x2="40" y2="120" stroke="currentColor" strokeOpacity="0.2" />
      <line x1="160" y1="40" x2="160" y2="120" stroke="currentColor" strokeOpacity="0.2" />
      <circle cx="100" cy="80" r="8" fill="currentColor" />
      <circle cx="40" cy="40" r="5" fill="currentColor" opacity="0.7" />
      <circle cx="160" cy="40" r="5" fill="currentColor" opacity="0.7" />
      <circle cx="40" cy="120" r="5" fill="currentColor" opacity="0.7" />
      <circle cx="160" cy="120" r="5" fill="currentColor" opacity="0.7" />
      <circle cx="100" cy="20" r="4" fill="currentColor" opacity="0.5" />
      <circle cx="100" cy="140" r="4" fill="currentColor" opacity="0.5" />
    </svg>
  );
}
