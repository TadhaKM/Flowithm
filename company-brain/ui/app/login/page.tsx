"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export default function LoginPage() {
  const router = useRouter();
  const supabaseRef = useRef<ReturnType<typeof createClient> | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // Create the Supabase client on mount (client-side only — avoids the
  // prerender crash when NEXT_PUBLIC_ vars are not yet inlined).
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
    if (!email.trim() || !password) {
      setError("Email and password are required.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const { error: authErr } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (authErr) throw authErr;
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
            Sign in
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            Welcome back. Sign in to your knowledge base.
          </p>

          <label className="mt-5 block text-xs uppercase tracking-wider text-zinc-500">
            Email
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            placeholder="you@company.com"
            autoFocus
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

          {error && (
            <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {error}
            </div>
          )}

          <button
            onClick={submit}
            disabled={pending || !email.trim() || !password}
            className="mt-6 w-full rounded-md bg-[#1D9E75] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#178c66] disabled:opacity-50 transition-colors"
          >
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </div>

        <p className="mt-6 text-center text-sm text-zinc-500">
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="text-zinc-300 hover:text-white transition-colors">
            Sign up
          </Link>
        </p>
      </div>
    </main>
  );
}
