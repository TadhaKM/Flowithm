"use client";

// Step 3 of the first-run flow. Two modes driven by the `?source` query:
//
//   manual  -> textarea pre-filled with demo material; user can edit
//              and click Generate immediately
//   slack/notion/gmail -> three "suggested processes" chips that
//              pre-fill the process_name; user pastes a representative
//              thread/page into the textarea and clicks Generate.
//
// Auto-RAG-from-chunks isn't viable here — a brand-new connected
// source has no chunks yet, and generate_workflow_from_text takes raw
// text rather than a query. The chips give the user a sensible starting
// process_name; they paste a real example to ground the generation.
//
// On success, fires confetti, marks onboarding complete in localStorage,
// and lets the user jump to /brain or generate another.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import StepIndicator from "../_components/StepIndicator";

type SourceType = "slack" | "notion" | "gmail" | "manual";

const SUGGESTIONS: Record<Exclude<SourceType, "manual">, string[]> = {
  slack: ["Incident response", "Customer escalation", "Deploy process"],
  notion: ["Onboarding", "Offboarding", "Review process"],
  gmail: ["Customer support", "Vendor approval", "Contract review"],
};

const DEMO_PASTE = `Subject: Refund window expansion

From: Marcus Holt (CTO)

Effective immediately, customer refunds are approved within 60 days
of purchase for all customers, regardless of plan type. The previous
30-day limit is replaced.

- Step 1: Verify the request was filed within 60 days of purchase.
- Step 2: Process the refund via Stripe directly. CFO approval is
  no longer required for refunds under $5,000.

Annual contracts now follow the same 60-day window — the prior
"no cash refund mid-term" rule is removed.`;

export default function OnboardingGeneratePage() {
  const router = useRouter();
  const params = useSearchParams();
  const source = (params.get("source") || "manual") as SourceType;

  const [processName, setProcessName] = useState("");
  const [content, setContent] = useState(source === "manual" ? DEMO_PASTE : "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ id: string; process: string; version: number } | null>(null);

  // Reset to a fresh form (after the user clicks Generate another).
  function reset() {
    setProcessName("");
    setContent(source === "manual" ? DEMO_PASTE : "");
    setError(null);
    setSuccess(null);
  }

  async function generate() {
    if (pending) return;
    if (!processName.trim() || !content.trim()) {
      setError("Process name + source material both required.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/workflows/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: processName.trim(),
          content,
          source: source === "manual" ? "manual" : source,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);

      try {
        window.localStorage.setItem("flowithm_onboarding_step", "complete");
      } catch { /* ignore */ }

      setSuccess({
        id: body.id || "",
        process: body.process || processName.trim(),
        version: body.version || 1,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8 text-center">
          <Link href="/" className="text-2xl font-medium tracking-tight text-zinc-100">
            Flowithm
          </Link>
        </div>

        <StepIndicator active="generate" />

        {success ? (
          <SuccessState
            process={success.process}
            onView={() => router.push("/brain")}
            onAnother={reset}
          />
        ) : (
          <GenerateForm
            source={source}
            processName={processName}
            setProcessName={setProcessName}
            content={content}
            setContent={setContent}
            pending={pending}
            error={error}
            onGenerate={generate}
          />
        )}
      </div>
    </main>
  );
}

function GenerateForm({
  source,
  processName,
  setProcessName,
  content,
  setContent,
  pending,
  error,
  onGenerate,
}: {
  source: SourceType;
  processName: string;
  setProcessName: (v: string) => void;
  content: string;
  setContent: (v: string) => void;
  pending: boolean;
  error: string | null;
  onGenerate: () => void;
}) {
  const suggestions = source === "manual" ? null : SUGGESTIONS[source];
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-7 shadow-xl shadow-black/20">
      <h1 className="text-xl font-medium tracking-tight text-zinc-100">
        Generate your first workflow
      </h1>
      <p className="mt-2 text-sm text-zinc-500">
        {source === "manual"
          ? "We've pre-filled some sample material. Edit it, give it a name, and Flowithm will distil it into a structured workflow."
          : "Pick a process to start with. Then paste a representative thread or page so Flowithm has something concrete to learn from."}
      </p>

      {suggestions && (
        <div className="mt-5">
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">
            Suggested processes
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s}
                onClick={() => setProcessName(s)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                  processName === s
                    ? "border-[#1D9E75] bg-[#1D9E75]/10 text-[#1D9E75]"
                    : "border-zinc-700 text-zinc-300 hover:border-zinc-500"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      <label className="mt-5 block text-xs uppercase tracking-wider text-zinc-500">
        Process name
      </label>
      <input
        value={processName}
        onChange={(e) => setProcessName(e.target.value)}
        placeholder="e.g. Customer refund handling"
        className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
      />

      <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
        Source material
      </label>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={10}
        placeholder="Paste a thread, runbook section, or policy memo. The richer the material, the better the workflow."
        className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
      />

      {error && (
        <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
          {error}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <button
          onClick={() => {
            try { window.localStorage.setItem("flowithm_onboarding_step", "complete"); } catch { /* ignore */ }
            window.location.href = "/brain";
          }}
          className="text-xs text-zinc-500 hover:text-zinc-300 underline-offset-4 hover:underline transition-colors"
        >
          Skip — go to dashboard →
        </button>
        <button
          onClick={onGenerate}
          disabled={pending || !processName.trim() || !content.trim()}
          className="rounded-md bg-[#1D9E75] px-4 py-2 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
        >
          {pending ? "Generating…" : "Generate workflow →"}
        </button>
      </div>
    </div>
  );
}

function SuccessState({
  process,
  onView,
  onAnother,
}: {
  process: string;
  onView: () => void;
  onAnother: () => void;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-[#1D9E75]/40 bg-zinc-900/60 p-10 text-center shadow-xl shadow-black/20">
      <Confetti />
      <div className="relative">
        <div className="text-xs uppercase tracking-wider text-[#1D9E75] mb-3">
          ✓ Workflow generated
        </div>
        <h2 className="text-2xl font-medium tracking-tight text-zinc-100">
          Your first workflow is ready
        </h2>
        <p className="mt-2 text-sm text-zinc-500">
          <span className="text-zinc-300">{process}</span>{" "}
          is now in your knowledge base.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
          <button
            onClick={onView}
            className="rounded-md bg-[#1D9E75] px-4 py-2 text-sm font-medium text-white hover:bg-[#178c66] transition-colors"
          >
            View your knowledge base →
          </button>
          <button
            onClick={onAnother}
            className="rounded-md border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-800 transition-colors"
          >
            Generate another
          </button>
        </div>
      </div>
    </div>
  );
}

// Confetti — pure CSS, no library. Renders a fixed set of pieces with
// stable randomised positions/colours/delays once on mount, then plays
// the keyframes once. Because it lives inside an overflow-hidden parent,
// pieces clip cleanly at the edges.
function Confetti() {
  const pieces = useMemo(() => {
    const colors = ["#1D9E75", "#34d399", "#f59e0b", "#a78bfa", "#60a5fa", "#f87171"];
    return Array.from({ length: 36 }).map((_, i) => ({
      id: i,
      left: Math.random() * 100,
      delay: Math.random() * 1.5,
      duration: 1.6 + Math.random() * 1.4,
      color: colors[Math.floor(Math.random() * colors.length)],
      rotation: Math.random() * 360,
      size: 6 + Math.random() * 6,
    }));
  }, []);
  // Avoid emitting confetti during SSR (hydration sees random positions
  // mismatch); render only after mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return (
    <div className="pointer-events-none absolute inset-0">
      <style jsx>{`
        @keyframes flowithm-confetti-fall {
          0%   { transform: translate3d(0, -40px, 0) rotate(0deg); opacity: 1; }
          80%  { opacity: 1; }
          100% { transform: translate3d(20px, 220px, 0) rotate(720deg); opacity: 0; }
        }
      `}</style>
      {pieces.map((p) => (
        <span
          key={p.id}
          style={{
            position: "absolute",
            top: 0,
            left: `${p.left}%`,
            width: p.size,
            height: p.size,
            background: p.color,
            borderRadius: 1,
            transform: `rotate(${p.rotation}deg)`,
            animation: `flowithm-confetti-fall ${p.duration}s ease-in ${p.delay}s forwards`,
          }}
        />
      ))}
    </div>
  );
}
