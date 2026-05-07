"use client";

// Step 2 of the first-run flow. Pick a source to connect (or skip
// straight to manual paste). Slack / Notion / Gmail expand inline
// to show their config form; submitting POSTs to /api/admin/sources
// then advances to /onboarding/generate?source=<type>. Manual paste
// skips the source-creation entirely and jumps straight to step 3.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import StepIndicator from "../_components/StepIndicator";

type SourceType = "slack" | "notion" | "gmail" | "manual";

const SOURCES: {
  type: SourceType;
  name: string;
  description: string;
}[] = [
  { type: "slack", name: "Slack", description: "Threads where decisions actually happen." },
  { type: "notion", name: "Notion", description: "Runbooks, policies, the Loose Internal Wiki." },
  { type: "gmail", name: "Gmail", description: "Escalations and customer threads worth keeping." },
  { type: "manual", name: "Manual paste", description: "Skip OAuth — paste source material directly." },
];

export default function OnboardingConnectPage() {
  const router = useRouter();
  const [expanded, setExpanded] = useState<SourceType | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Per-source form state. Kept in one bag so the same fields stay
  // populated if the user toggles between cards.
  const [displayName, setDisplayName] = useState("");
  const [token, setToken] = useState("");
  const [idsText, setIdsText] = useState("");
  const [credsJson, setCredsJson] = useState("");
  const [labelsText, setLabelsText] = useState("");

  function setStepAndGo(step: "generate", source: SourceType) {
    try {
      window.localStorage.setItem("flowithm_onboarding_step", step);
    } catch { /* ignore */ }
    router.push(`/onboarding/generate?source=${source}`);
  }

  function pickCard(type: SourceType) {
    setError(null);
    if (type === "manual") {
      setStepAndGo("generate", "manual");
      return;
    }
    setExpanded(expanded === type ? null : type);
  }

  async function submit(type: Exclude<SourceType, "manual">) {
    if (pending) return;
    setError(null);
    if (!displayName.trim()) {
      setError("Display name required.");
      return;
    }

    let config: Record<string, unknown>;
    if (type === "slack") {
      const ids = idsText.split(",").map((s) => s.trim()).filter(Boolean);
      if (!token.trim() || ids.length === 0) {
        setError("Bot token + at least one channel ID required.");
        return;
      }
      config = { bot_token: token.trim(), channel_ids: ids };
    } else if (type === "notion") {
      const ids = idsText.split(",").map((s) => s.trim()).filter(Boolean);
      if (!token.trim() || ids.length === 0) {
        setError("Integration token + at least one page ID required.");
        return;
      }
      config = { integration_token: token.trim(), page_ids: ids };
    } else {
      // gmail
      const labels = labelsText.split(",").map((s) => s.trim()).filter(Boolean);
      if (!credsJson.trim() || labels.length === 0) {
        setError("Credentials JSON + at least one label required.");
        return;
      }
      let parsed: string;
      try {
        parsed = JSON.stringify(JSON.parse(credsJson));
      } catch {
        setError("Credentials JSON isn't valid JSON.");
        return;
      }
      config = { credentials_json: parsed, label_filters: labels, min_thread_length: 2 };
    }

    setPending(true);
    try {
      const res = await fetch("/api/admin/sources", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_type: type,
          display_name: displayName.trim(),
          config,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
      setStepAndGo("generate", type);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPending(false);
    }
  }

  function skipForNow() {
    try {
      // Mark generate step as the next thing the user could do, but the
      // banner on /brain will keep nudging until they generate something.
      window.localStorage.setItem("flowithm_onboarding_step", "generate");
    } catch { /* ignore */ }
    router.push("/brain");
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8 text-center">
          <Link href="/" className="text-2xl font-medium tracking-tight text-zinc-100">
            Flowithm
          </Link>
        </div>

        <StepIndicator active="connect" />

        <div className="mb-8 text-center">
          <h1 className="text-2xl font-medium tracking-tight text-zinc-100">
            Connect your first source
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Flowithm learns from where your company already communicates.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {SOURCES.map((s) => (
            <SourceCard
              key={s.type}
              source={s}
              expanded={expanded === s.type}
              onClick={() => pickCard(s.type)}
            >
              {expanded === s.type && s.type !== "manual" && (() => {
                const t = s.type as Exclude<SourceType, "manual">;
                return (
                  <ExpandedForm
                    type={t}
                    displayName={displayName}
                    setDisplayName={setDisplayName}
                    token={token}
                    setToken={setToken}
                    idsText={idsText}
                    setIdsText={setIdsText}
                    credsJson={credsJson}
                    setCredsJson={setCredsJson}
                    labelsText={labelsText}
                    setLabelsText={setLabelsText}
                    pending={pending}
                    onSubmit={() => submit(t)}
                  />
                );
              })()}
            </SourceCard>
          ))}
        </div>

        {error && (
          <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <div className="mt-8 text-center">
          <button
            onClick={skipForNow}
            className="text-xs text-zinc-500 hover:text-zinc-300 underline-offset-4 hover:underline transition-colors"
          >
            Skip for now, I&apos;ll connect later →
          </button>
        </div>
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------

function SourceCard({
  source,
  expanded,
  onClick,
  children,
}: {
  source: { type: SourceType; name: string; description: string };
  expanded: boolean;
  onClick: () => void;
  children?: React.ReactNode;
}) {
  return (
    <article
      className={`rounded-xl border bg-zinc-900/60 p-5 transition-colors ${
        expanded
          ? "border-[#1D9E75]/50"
          : "border-zinc-800 hover:border-zinc-700"
      } ${expanded ? "sm:col-span-2" : ""}`}
    >
      <button
        onClick={onClick}
        className="flex w-full items-center gap-4 text-left"
      >
        <SourceMark type={source.type} />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-zinc-100">{source.name}</div>
          <div className="text-xs text-zinc-500 truncate">{source.description}</div>
        </div>
        <span className="text-xs font-medium text-[#1D9E75]">
          {source.type === "manual"
            ? "Use →"
            : expanded
              ? "Cancel"
              : "Connect"}
        </span>
      </button>
      {children}
    </article>
  );
}

function ExpandedForm({
  type,
  displayName,
  setDisplayName,
  token,
  setToken,
  idsText,
  setIdsText,
  credsJson,
  setCredsJson,
  labelsText,
  setLabelsText,
  pending,
  onSubmit,
}: {
  type: "slack" | "notion" | "gmail";
  displayName: string;
  setDisplayName: (v: string) => void;
  token: string;
  setToken: (v: string) => void;
  idsText: string;
  setIdsText: (v: string) => void;
  credsJson: string;
  setCredsJson: (v: string) => void;
  labelsText: string;
  setLabelsText: (v: string) => void;
  pending: boolean;
  onSubmit: () => void;
}) {
  return (
    <div className="mt-4 border-t border-zinc-800 pt-4">
      <FieldLabel>Display name</FieldLabel>
      <input
        value={displayName}
        onChange={(e) => setDisplayName(e.target.value)}
        placeholder={
          type === "slack" ? "Engineering Slack"
            : type === "notion" ? "Product Wiki"
            : "Support inbox"
        }
        className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
      />

      {(type === "slack" || type === "notion") && (
        <>
          <FieldLabel>{type === "slack" ? "Bot token (xoxb-…)" : "Integration token (secret_…)"}</FieldLabel>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="paste token here"
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
          />
          <FieldLabel>{type === "slack" ? "Channel IDs (comma-separated)" : "Page IDs (comma-separated)"}</FieldLabel>
          <input
            value={idsText}
            onChange={(e) => setIdsText(e.target.value)}
            placeholder={type === "slack" ? "C0123ABC, C4567DEF" : "abc123…, def456…"}
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
          />
        </>
      )}

      {type === "gmail" && (
        <>
          <FieldLabel>Credentials JSON</FieldLabel>
          <textarea
            value={credsJson}
            onChange={(e) => setCredsJson(e.target.value)}
            placeholder='{"token":"…","refresh_token":"…",…}'
            rows={5}
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
          />
          <p className="mt-1 text-[11px] text-zinc-500">
            Run <code className="text-zinc-300">python -m ingest.gmail_auth</code> to generate.
          </p>

          <FieldLabel>Label filters (comma-separated)</FieldLabel>
          <input
            value={labelsText}
            onChange={(e) => setLabelsText(e.target.value)}
            placeholder="process, policy, escalation"
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none font-mono"
          />
        </>
      )}

      <div className="mt-4 flex justify-end">
        <button
          onClick={onSubmit}
          disabled={pending}
          className="rounded-md bg-[#1D9E75] px-4 py-2 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
        >
          {pending ? "Connecting…" : "Connect & continue"}
        </button>
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

function SourceMark({ type }: { type: SourceType }) {
  const palette: Record<SourceType, string> = {
    slack: "bg-purple-500/15 text-purple-200 border-purple-500/30",
    notion: "bg-zinc-700/40 text-zinc-200 border-zinc-600",
    gmail: "bg-red-500/15 text-red-200 border-red-500/30",
    manual: "bg-[#1D9E75]/15 text-[#1D9E75] border-[#1D9E75]/30",
  };
  return (
    <div
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md border text-sm font-semibold ${palette[type]}`}
    >
      {type.charAt(0).toUpperCase()}
    </div>
  );
}
