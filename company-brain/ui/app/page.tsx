"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SUGGESTED_QUESTIONS = [
  "How do we handle a customer refund?",
  "What's the on-call runbook for a DB outage?",
  "What is our remote work policy?",
];

type Confidence = "high" | "medium" | "low";

type Source = {
  source_type: string;
  source_name: string;
  content_preview: string;
};

type Answer = {
  answer: string;
  sources: Source[];
  confidence: Confidence;
};

type SkillStep = {
  step: number;
  action: string;
  owner: string;
  notes: string;
};

type SkillFile = {
  process: string;
  description: string;
  steps: SkillStep[];
  decision_rules: string[];
  exceptions: string[];
  sources: string[];
};

export default function Home() {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [skill, setSkill] = useState<SkillFile | null>(null);
  const [skillLoading, setSkillLoading] = useState(false);
  const [skillModalOpen, setSkillModalOpen] = useState(false);

  async function ask(question: string) {
    if (!question.trim() || loading) return;
    setSubmitted(question);
    setLoading(true);
    setError(null);
    setAnswer(null);
    setSkill(null);
    try {
      const res = await fetch(`${API_URL}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!res.ok) {
        throw new Error(`API ${res.status}: ${await res.text()}`);
      }
      const data = (await res.json()) as Answer;
      setAnswer(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function generateSkill() {
    if (!submitted || skillLoading) return;
    setSkillLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/skills`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ process_name: submitted }),
      });
      if (!res.ok) {
        throw new Error(`API ${res.status}: ${await res.text()}`);
      }
      const data = (await res.json()) as SkillFile;
      setSkill(data);
      setSkillModalOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSkillLoading(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    ask(input);
  }

  function pickSuggested(q: string) {
    setInput(q);
    ask(q);
  }

  return (
    <main className="min-h-screen relative">
      <div className="bg-glow absolute inset-x-0 top-0 h-[400px] pointer-events-none" />

      <div className="relative max-w-3xl mx-auto px-4 sm:px-6 pt-16 sm:pt-24 pb-24">
        <header className="text-center mb-10">
          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight bg-clip-text text-transparent bg-gradient-to-b from-zinc-50 to-zinc-400">
            Company Brain
          </h1>
          <p className="mt-3 text-zinc-400 text-base sm:text-lg">
            Ask anything about how Loopline works
          </p>
        </header>

        <form onSubmit={onSubmit} className="mb-3">
          <div className="flex items-center gap-2 bg-zinc-900/80 backdrop-blur border border-zinc-800 rounded-2xl p-2 focus-within:border-indigo-500/60 focus-within:bg-zinc-900 transition-colors shadow-lg shadow-black/30">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question..."
              disabled={loading}
              autoFocus
              className="flex-1 bg-transparent px-4 py-3 text-base sm:text-lg placeholder-zinc-500 focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="inline-flex items-center gap-1.5 bg-indigo-500 hover:bg-indigo-400 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-4 py-2.5 rounded-xl transition-colors shadow-md shadow-indigo-500/20"
            >
              {loading ? (
                <Spinner />
              ) : (
                <>
                  <span>Send</span>
                  <SendIcon />
                </>
              )}
            </button>
          </div>
        </form>

        <div className="flex flex-wrap gap-2 mb-10 justify-center">
          {SUGGESTED_QUESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => pickSuggested(q)}
              disabled={loading}
              className="text-xs sm:text-sm bg-zinc-900/60 hover:bg-zinc-800 border border-zinc-800 hover:border-zinc-700 text-zinc-300 hover:text-zinc-100 px-3 py-1.5 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {q}
            </button>
          ))}
        </div>

        {error && (
          <div className="bg-rose-950/40 border border-rose-900/60 text-rose-200 rounded-xl p-4 mb-6 text-sm">
            <strong className="font-medium">Error.</strong> {error}
          </div>
        )}

        {loading && <LoadingSkeleton />}

        {!loading && answer && (
          <AnswerCard
            answer={answer}
            onGenerateSkill={generateSkill}
            skillLoading={skillLoading}
          />
        )}
      </div>

      {skillModalOpen && skill && (
        <SkillsModal skill={skill} onClose={() => setSkillModalOpen(false)} />
      )}
    </main>
  );
}

// --------------------------------------------------------------------------
// Components
// --------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-6 animate-pulse">
      <div className="flex items-center justify-between mb-5">
        <div className="h-3 w-16 bg-zinc-800 rounded" />
        <div className="h-5 w-28 bg-zinc-800 rounded-full" />
      </div>
      <div className="space-y-3">
        <div className="h-4 bg-zinc-800 rounded w-full" />
        <div className="h-4 bg-zinc-800 rounded w-11/12" />
        <div className="h-4 bg-zinc-800 rounded w-4/5" />
        <div className="h-4 bg-zinc-800 rounded w-3/4" />
      </div>
      <div className="flex gap-2 mt-6 pt-5 border-t border-zinc-800">
        <div className="h-7 w-32 bg-zinc-800 rounded-full" />
        <div className="h-7 w-28 bg-zinc-800 rounded-full" />
        <div className="h-7 w-24 bg-zinc-800 rounded-full" />
      </div>
    </div>
  );
}

function AnswerCard({
  answer,
  onGenerateSkill,
  skillLoading,
}: {
  answer: Answer;
  onGenerateSkill: () => void;
  skillLoading: boolean;
}) {
  return (
    <article className="bg-zinc-900/80 backdrop-blur border border-zinc-800 rounded-2xl p-6 shadow-xl shadow-black/30">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-xs uppercase tracking-wider text-zinc-500 font-medium">
          Answer
        </h2>
        <ConfidenceBadge level={answer.confidence} />
      </div>

      <div className="text-zinc-100 leading-relaxed whitespace-pre-wrap text-[15px]">
        {answer.answer}
      </div>

      {answer.sources.length > 0 && (
        <div className="mt-6 pt-5 border-t border-zinc-800">
          <h3 className="text-xs uppercase tracking-wider text-zinc-500 font-medium mb-3">
            Sources
          </h3>
          <div className="flex flex-wrap gap-2">
            {answer.sources.map((s, i) => (
              <SourcePill key={i} source={s} index={i + 1} />
            ))}
          </div>
        </div>
      )}

      <div className="mt-6 pt-5 border-t border-zinc-800 flex justify-end">
        <button
          onClick={onGenerateSkill}
          disabled={skillLoading}
          className="inline-flex items-center gap-2 text-sm bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed border border-zinc-700 hover:border-zinc-600 text-zinc-100 px-4 py-2 rounded-lg transition-colors"
        >
          {skillLoading ? (
            <>
              <Spinner /> Generating skills file...
            </>
          ) : (
            <>
              <SparkleIcon /> Generate Skills File
            </>
          )}
        </button>
      </div>
    </article>
  );
}

function ConfidenceBadge({ level }: { level: Confidence }) {
  const styles: Record<Confidence, { wrap: string; dot: string }> = {
    high: {
      wrap: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
      dot: "bg-emerald-400",
    },
    medium: {
      wrap: "bg-amber-500/10 text-amber-300 border-amber-500/30",
      dot: "bg-amber-400",
    },
    low: {
      wrap: "bg-rose-500/10 text-rose-300 border-rose-500/30",
      dot: "bg-rose-400",
    },
  };
  const s = styles[level];
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest font-medium border rounded-full px-2.5 py-1 ${s.wrap}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot} animate-pulse`} />
      {level} confidence
    </span>
  );
}

function SourcePill({ source, index }: { source: Source; index: number }) {
  return (
    <span
      className="group inline-flex items-center gap-1.5 text-xs bg-zinc-800/80 hover:bg-zinc-800 border border-zinc-700/60 hover:border-zinc-700 rounded-full pl-2 pr-3 py-1 transition-colors cursor-default max-w-full"
      title={source.content_preview}
    >
      <span className="text-zinc-500 text-[10px] font-mono">[{index}]</span>
      <SourceIcon type={source.source_type} />
      <span className="text-zinc-300 group-hover:text-zinc-100 truncate">
        {source.source_name}
      </span>
    </span>
  );
}

function SourceIcon({ type }: { type: string }) {
  const cls = "w-3.5 h-3.5 text-zinc-400 shrink-0";
  const t = type.toLowerCase();
  if (t === "slack") return <SlackIcon className={cls} />;
  if (t === "notion") return <NotionIcon className={cls} />;
  if (t === "github") return <GitHubIcon className={cls} />;
  return <DocIcon className={cls} />;
}

function SkillsModal({
  skill,
  onClose,
}: {
  skill: SkillFile;
  onClose: () => void;
}) {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const json = JSON.stringify(skill, null, 2);
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(json);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore — clipboard might be blocked over http
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-zinc-800">
          <div className="min-w-0">
            <h2 className="text-base font-medium text-zinc-100">Skills file</h2>
            <p className="text-xs text-zinc-500 mt-0.5 truncate">
              {skill.process}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={copy}
              className="text-xs bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 px-2.5 py-1.5 rounded-md transition-colors"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              onClick={onClose}
              className="text-zinc-400 hover:text-zinc-100 transition-colors p-1 rounded"
              aria-label="Close"
            >
              <CloseIcon />
            </button>
          </div>
        </div>
        <div className="overflow-auto scrollbar-thin p-4 flex-1">
          <pre
            className="text-[12.5px] sm:text-sm font-mono leading-relaxed text-zinc-300 whitespace-pre"
            dangerouslySetInnerHTML={{ __html: highlightJson(json) }}
          />
        </div>
      </div>
    </div>
  );
}

// JSON.stringify is safe to render after HTML-escaping the string. The
// regex then wraps tokens in <span> tags for color. Tokens never contain
// raw HTML because we escape `<`, `>`, `&` first.
function highlightJson(json: string): string {
  const escaped = json
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  return escaped.replace(
    /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = "text-emerald-400"; // number
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? "text-sky-400" : "text-amber-300";
      } else if (match === "true" || match === "false") {
        cls = "text-purple-400";
      } else if (match === "null") {
        cls = "text-rose-400";
      }
      return `<span class="${cls}">${match}</span>`;
    },
  );
}

// --------------------------------------------------------------------------
// Icons (inline SVG — no icon library)
// --------------------------------------------------------------------------

function SendIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 5l7 7-7 7" />
    </svg>
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

function SparkleIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
      <path d="M19 15l.7 1.7L21.5 17.5l-1.8.8L19 20l-.7-1.7L16.5 17.5l1.8-.8z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function SlackIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor">
      <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" />
    </svg>
  );
}

function NotionIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor">
      <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952l1.448.327s0 .84-1.169.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933z" />
    </svg>
  );
}

function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

function DocIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}
