"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export default function SignupPage() {
  const router = useRouter();
  const supabaseRef = useRef<ReturnType<typeof createClient> | null>(null);
  const [companyName, setCompanyName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    supabaseRef.current = createClient();
    supabaseRef.current.auth.getUser().then(({ data: { user } }) => {
      if (user) {
        router.replace("/brain");
      } else {
        setReady(true);
      }
    });
  }, [router]);

  async function submit() {
    const supabase = supabaseRef.current;
    if (!supabase || pending) return;
    if (!companyName.trim()) {
      setError("Company name is required.");
      return;
    }
    if (!email.trim() || !password) {
      setError("Email and password are required.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setPending(true);
    setError(null);

    try {
      // 1. Create the Supabase Auth user.
      const { error: signUpErr } = await supabase.auth.signUp({
        email: email.trim(),
        password,
      });
      if (signUpErr) throw signUpErr;

      // 2. Create the organisation + link the user on the server.
      const res = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          company_name: companyName.trim(),
          display_name: displayName.trim() || undefined,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body?.error || `Setup failed: HTTP ${res.status}`);
      }

      router.push("/brain");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setPending(false);
    }
  }

  if (!ready) {
    return (
      <main className="min-h-screen flex items-center justify-center bg-zinc-950">
        <div className="text-sm text-zinc-500">Loading…</div>
      </main>
    );
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
            Create your account
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Set up a new knowledge base for your team.
          </p>

          <label className="mt-5 block text-xs uppercase tracking-wider text-zinc-500">
            Company name
          </label>
          <input
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            placeholder="Loopline"
            autoFocus
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />

          <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
            Email
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            placeholder="you@company.com"
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />

          <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            placeholder="••••••••"
            className="mt-1 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-[#1D9E75] focus:outline-none"
          />

          <label className="mt-3 block text-xs uppercase tracking-wider text-zinc-500">
            Your name <span className="ml-1 text-zinc-600 normal-case">(optional)</span>
          </label>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
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
            disabled={pending || !companyName.trim() || !email.trim() || !password}
            className="mt-6 w-full rounded-md bg-[#1D9E75] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
          >
            {pending ? "Creating account…" : "Create account"}
          </button>
        </div>

        <p className="mt-6 text-center text-sm text-zinc-500">
          Already have an account?{" "}
          <Link href="/login" className="text-zinc-300 hover:text-white transition-colors">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
