"use client";

// First-run setup. Renders when no `flowithm_org_id` cookie is present.
// Submits {company_name, user_name?} to /api/setup which creates the
// organisation server-side and sets the httpOnly cookie. After success,
// the user is redirected to /brain — every subsequent admin proxy
// request reads the cookie and forwards X-Org-ID to FastAPI.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function SetupPage() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [userName, setUserName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (pending) return;
    if (!companyName.trim()) {
      setError("Company name is required.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const res = await fetch("/api/setup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          company_name: companyName.trim(),
          user_name: userName.trim() || undefined,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body?.error || `HTTP ${res.status}`);
      }
      router.push("/brain");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPending(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-950 px-6">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <Link href="/" className="text-2xl font-medium tracking-tight text-zinc-100">
            Flowithm
          </Link>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-7 shadow-xl shadow-black/20">
          <h1 className="text-xl font-medium tracking-tight text-zinc-100">
            Set up your knowledge base
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Takes 2 minutes. You can connect sources later.
          </p>

          <label className="mt-5 block text-xs uppercase tracking-wider text-zinc-500">
            Company name
          </label>
          <input
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            placeholder="Loopline"
            autoFocus
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />

          <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
            Your name <span className="ml-1 text-zinc-600 normal-case">(optional)</span>
          </label>
          <input
            value={userName}
            onChange={(e) => setUserName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            placeholder="Sarah"
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />

          {error && (
            <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {error}
            </div>
          )}

          <button
            onClick={submit}
            disabled={pending || !companyName.trim()}
            className="mt-6 w-full rounded-md bg-[#1D9E75] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
          >
            {pending ? "Setting up…" : "Get started →"}
          </button>
        </div>

        <p className="mt-6 text-center text-xs text-zinc-600">
          Your data is fully isolated. You can connect Slack, Notion, Gmail,
          Intercom, and GitHub as sources after setup.
        </p>
      </div>
    </main>
  );
}
