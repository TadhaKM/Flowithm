// Public marketing landing page, shown at "/" to signed-out visitors.
// Signed-in users get the generator app instead (see app/page.tsx).
// Server component — no interactivity beyond links, so no "use client".
import Link from "next/link";

export function LandingPage() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      <BackgroundGlow />

      {/* ---------------------------------------------------------------- */}
      {/* Nav                                                              */}
      {/* ---------------------------------------------------------------- */}
      <nav className="sticky top-0 z-40 border-b border-zinc-800/60 bg-zinc-950/70 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-base font-semibold tracking-tight text-zinc-100"
          >
            <LogoMark />
            Flowithm
          </Link>
          <div className="hidden items-center gap-7 text-sm text-zinc-400 md:flex">
            <a href="#how-it-works" className="transition-colors hover:text-zinc-100">
              How it works
            </a>
            <a href="#features" className="transition-colors hover:text-zinc-100">
              Features
            </a>
            <a href="#security" className="transition-colors hover:text-zinc-100">
              Security
            </a>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="text-sm text-zinc-400 transition-colors hover:text-zinc-100"
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="rounded-lg bg-[#1D9E75] px-4 py-2 text-sm font-medium text-white shadow-lg shadow-[#1D9E75]/20 transition-colors hover:bg-[#25b88a]"
            >
              Get started
            </Link>
          </div>
        </div>
      </nav>

      {/* ---------------------------------------------------------------- */}
      {/* Hero                                                             */}
      {/* ---------------------------------------------------------------- */}
      <section className="relative mx-auto max-w-6xl px-6 pb-24 pt-20 text-center sm:pt-28">
        <div className="animate-fade-in">
          <span className="inline-flex items-center gap-2 rounded-full border border-[#1D9E75]/30 bg-[#1D9E75]/10 px-3.5 py-1.5 text-xs font-medium tracking-wide text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Knowledge in. Workflows out.
          </span>

          <h1 className="mx-auto mt-7 max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight text-zinc-50 sm:text-6xl">
            Turn tribal knowledge into workflows{" "}
            <span className="bg-gradient-to-r from-emerald-300 via-[#25b88a] to-teal-300 bg-clip-text text-transparent">
              AI can run
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-zinc-400 sm:text-lg">
            Your best processes live in Slack threads, old docs, and people&apos;s
            heads. Flowithm ingests them, extracts the real decision rules, and
            produces structured, executable workflows — for humans and AI agents
            alike.
          </p>

          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              href="/signup"
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#1D9E75] px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-[#1D9E75]/25 transition-all hover:bg-[#25b88a] hover:shadow-[#1D9E75]/40 sm:w-auto"
            >
              Get started free
              <ArrowRight />
            </Link>
            <Link
              href="/login"
              className="inline-flex w-full items-center justify-center rounded-xl border border-zinc-700 bg-zinc-900/60 px-7 py-3.5 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-800 sm:w-auto"
            >
              Sign in
            </Link>
          </div>

          <p className="mt-5 text-xs text-zinc-600">
            No credit card required · Set up in minutes
          </p>
        </div>

        {/* Product visual */}
        <div className="relative mx-auto mt-16 max-w-4xl">
          <div className="absolute -inset-x-8 -top-8 h-40 bg-[#1D9E75]/10 blur-3xl" />
          <HeroMock />
        </div>

        {/* Source row */}
        <div className="mt-16">
          <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-zinc-600">
            Pulls knowledge from the tools you already use
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-10 gap-y-4 text-sm font-medium text-zinc-500">
            {["Slack", "Notion", "GitHub", "Gmail", "Intercom"].map((s) => (
              <span
                key={s}
                className="flex items-center gap-2 transition-colors hover:text-zinc-300"
              >
                <SourceDot />
                {s}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* How it works                                                     */}
      {/* ---------------------------------------------------------------- */}
      <section id="how-it-works" className="border-t border-zinc-800/60 bg-zinc-950/50">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <SectionHeading
            eyebrow="How it works"
            title="From scattered context to executable process"
            subtitle="Three steps between the knowledge you have and the workflows you need."
          />

          <div className="mt-14 grid gap-6 md:grid-cols-3">
            <StepCard
              step="01"
              title="Connect your sources"
              body="Point Flowithm at Slack, Notion, GitHub, Gmail, and Intercom. The scheduler keeps ingesting new threads, docs, and decisions on every cycle."
            />
            <StepCard
              step="02"
              title="Extraction, not summarization"
              body="Flowithm embeds everything into a vector knowledge base, then pulls out the triggers, steps, owners, approvals, and exception paths hiding in the noise."
            />
            <StepCard
              step="03"
              title="Export AI-executable skills"
              body="Every workflow ships as structured JSON a human can follow or an AI agent can execute — complete with decision rules and source citations."
            />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Features                                                         */}
      {/* ---------------------------------------------------------------- */}
      <section id="features" className="border-t border-zinc-800/60">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <SectionHeading
            eyebrow="Features"
            title="Built for the way work actually happens"
            subtitle="Real processes are messy. Flowithm is designed to capture the escalations, exceptions, and edge cases that never make it into the official docs."
          />

          <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <FeatureCard
              icon={<IconBolt />}
              title="Structured workflows"
              body="Triggers, ordered steps, owners, approvals, and exceptions — not a wall of prose."
            />
            <FeatureCard
              icon={<IconAgent />}
              title="AI-agent ready"
              body="Every workflow doubles as a machine-readable skill file with a confidence score attached."
            />
            <FeatureCard
              icon={<IconSearch />}
              title="Semantic knowledge base"
              body="Everything is embedded and searchable, so answers come with the sources they were built from."
            />
            <FeatureCard
              icon={<IconSync />}
              title="Always in sync"
              body="Connected sources re-ingest automatically. When the process changes in Slack, it changes in Flowithm."
            />
            <FeatureCard
              icon={<IconSlack />}
              title="Slack-native capture"
              body="Invite the bot to a channel and decisions get captured where they're made — no extra process."
            />
            <FeatureCard
              icon={<IconApi />}
              title="Agent API"
              body="A signed, org-scoped API lets your own agents query the knowledge base and execute skills safely."
            />
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Security                                                         */}
      {/* ---------------------------------------------------------------- */}
      <section id="security" className="border-t border-zinc-800/60 bg-zinc-950/50">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="grid items-center gap-12 lg:grid-cols-2">
            <div>
              <SectionHeading
                align="left"
                eyebrow="Security"
                title="Your knowledge stays yours"
                subtitle="Company knowledge is sensitive by definition. Flowithm treats it that way."
              />
              <ul className="mt-8 space-y-4">
                {[
                  "Org-scoped data isolation — every query and every skill execution is bound to your organization.",
                  "Signed admin requests with a dedicated signing key, never a shared token.",
                  "Bounded inputs on all public endpoints to shut down abuse before it starts.",
                  "You choose what gets ingested: label filters, tag watches, and per-source controls.",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3 text-sm leading-relaxed text-zinc-400">
                    <span className="mt-0.5 shrink-0 text-emerald-400">
                      <IconCheck />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>
              <Link
                href="/privacy"
                className="mt-8 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-300 transition-colors hover:text-emerald-200"
              >
                Read our privacy &amp; security overview
                <ArrowRight />
              </Link>
            </div>

            <div className="relative">
              <div className="absolute -inset-6 rounded-3xl bg-[#1D9E75]/5 blur-2xl" />
              <div className="relative rounded-2xl border border-zinc-800 bg-zinc-900/70 p-6 font-mono text-[12.5px] leading-7 text-zinc-400 shadow-2xl shadow-black/40">
                <div className="mb-4 flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                  <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                  <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
                </div>
                <p>
                  <span className="text-zinc-600">$</span> POST /agent/execute
                </p>
                <p className="text-zinc-600">→ X-Org-Id: acme-co</p>
                <p className="text-zinc-600">→ X-Admin-Sig: hmac-sha256 ✓</p>
                <p className="mt-3">
                  <span className="text-emerald-400">200 OK</span>{" "}
                  <span className="text-zinc-600">— scope: org, input: bounded</span>
                </p>
                <p className="mt-3 text-zinc-600">
                  {"{"} &quot;skill&quot;: <span className="text-violet-300">&quot;refund_policy&quot;</span>,
                </p>
                <p className="pl-4 text-zinc-600">
                  &quot;org&quot;: <span className="text-violet-300">&quot;acme-co&quot;</span>,
                </p>
                <p className="pl-4 text-zinc-600">
                  &quot;verified&quot;: <span className="text-emerald-400">true</span> {"}"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* Final CTA                                                        */}
      {/* ---------------------------------------------------------------- */}
      <section className="border-t border-zinc-800/60">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <div className="relative overflow-hidden rounded-3xl border border-[#1D9E75]/25 bg-gradient-to-br from-zinc-900 via-zinc-900 to-[#0d2a20] px-8 py-16 text-center shadow-2xl shadow-black/40 sm:px-16">
            <div className="pointer-events-none absolute -top-24 left-1/2 h-64 w-[36rem] -translate-x-1/2 rounded-full bg-[#1D9E75]/15 blur-3xl" />
            <h2 className="relative mx-auto max-w-2xl text-3xl font-semibold tracking-tight text-zinc-50 sm:text-4xl">
              Stop losing your best processes to scrollback
            </h2>
            <p className="relative mx-auto mt-4 max-w-xl text-base text-zinc-400">
              Connect a source, generate your first workflow, and hand your AI
              agents something they can actually execute.
            </p>
            <div className="relative mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                href="/signup"
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#1D9E75] px-7 py-3.5 text-sm font-semibold text-white shadow-lg shadow-[#1D9E75]/25 transition-all hover:bg-[#25b88a] sm:w-auto"
              >
                Get started free
                <ArrowRight />
              </Link>
              <Link
                href="/login"
                className="inline-flex w-full items-center justify-center rounded-xl border border-zinc-700 bg-zinc-950/40 px-7 py-3.5 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-800 sm:w-auto"
              >
                Sign in
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

// --------------------------------------------------------------------------
// Sections & cards
// --------------------------------------------------------------------------

function SectionHeading({
  eyebrow,
  title,
  subtitle,
  align = "center",
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  align?: "center" | "left";
}) {
  const centered = align === "center";
  return (
    <div className={centered ? "mx-auto max-w-2xl text-center" : "max-w-xl"}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-emerald-400">
        {eyebrow}
      </p>
      <h2 className="mt-3 text-3xl font-semibold tracking-tight text-zinc-50 sm:text-4xl">
        {title}
      </h2>
      <p className="mt-4 text-base leading-relaxed text-zinc-400">{subtitle}</p>
    </div>
  );
}

function StepCard({
  step,
  title,
  body,
}: {
  step: string;
  title: string;
  body: string;
}) {
  return (
    <div className="group relative rounded-2xl border border-zinc-800 bg-zinc-900/50 p-7 transition-colors hover:border-[#1D9E75]/40">
      <span className="font-mono text-sm font-semibold text-emerald-400/80">
        {step}
      </span>
      <h3 className="mt-4 text-lg font-medium text-zinc-100">{title}</h3>
      <p className="mt-3 text-sm leading-relaxed text-zinc-400">{body}</p>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="group rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6 transition-all hover:border-zinc-700 hover:bg-zinc-900/70">
      <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-[#1D9E75]/30 bg-[#1D9E75]/10 text-emerald-300">
        {icon}
      </div>
      <h3 className="mt-4 text-[15px] font-medium text-zinc-100">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-zinc-400">{body}</p>
    </div>
  );
}

// Stylized "before → after" product mock: raw Slack noise on the left,
// a structured workflow on the right.
function HeroMock() {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/80 shadow-2xl shadow-black/50 backdrop-blur">
      <div className="flex items-center gap-1.5 border-b border-zinc-800 px-5 py-3.5">
        <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
        <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
        <span className="h-2.5 w-2.5 rounded-full bg-zinc-700" />
        <span className="ml-3 text-xs text-zinc-600">
          flowithm — customer refund handling
        </span>
      </div>
      <div className="grid gap-px bg-zinc-800 md:grid-cols-2">
        {/* Left: messy input */}
        <div className="bg-zinc-950/70 p-6 text-left">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
            What you have
          </p>
          <div className="mt-4 space-y-3">
            <MockMessage
              author="#support"
              text="hey — customer wants a refund, order was 6 weeks ago, do we still do that??"
            />
            <MockMessage
              author="dana"
              text="over 30 days needs finance approval. under $200 support can just do it"
            />
            <MockMessage
              author="sam"
              text="unless it's an annual plan!! those always go through finance, we got burned last time"
            />
            <MockMessage author="dana" text="right, forgot about that 😅" />
          </div>
        </div>

        {/* Right: structured output */}
        <div className="bg-zinc-950/40 p-6 text-left">
          <div className="flex items-center gap-2">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">
              What you get
            </p>
            <span className="rounded-full border border-emerald-500/30 bg-emerald-500/15 px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-emerald-300">
              AI-Executable
            </span>
          </div>
          <div className="mt-4 space-y-2.5">
            <MockStep n={1} text="Check order age against the 30-day window" />
            <MockStep n={2} text="Under $200 and within window → support approves" />
            <MockStep n={3} text="Over 30 days or annual plan → route to finance" />
            <MockStep n={4} text="Log outcome and notify the customer" />
          </div>
          <div className="mt-4 rounded-lg border-l-2 border-amber-500 bg-amber-500/5 px-3 py-2 text-left text-xs text-amber-100/90">
            Approval required: finance sign-off for annual plans
          </div>
        </div>
      </div>
    </div>
  );
}

function MockMessage({ author, text }: { author: string; text: string }) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/70 px-3.5 py-2.5">
      <p className="text-[11px] font-semibold text-zinc-500">{author}</p>
      <p className="mt-0.5 text-[13px] leading-relaxed text-zinc-300">{text}</p>
    </div>
  );
}

function MockStep({ n, text }: { n: number; text: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-zinc-800/80 bg-zinc-900/60 px-3.5 py-2.5">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-[#1D9E75]/35 bg-[#1D9E75]/15 text-[11px] font-semibold text-emerald-300">
        {n}
      </span>
      <p className="text-[13px] leading-snug text-zinc-200">{text}</p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Decorative bits & icons
// --------------------------------------------------------------------------

function BackgroundGlow() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
      <div className="absolute left-1/2 top-[-14rem] h-[32rem] w-[56rem] -translate-x-1/2 rounded-full bg-[#1D9E75]/[0.09] blur-3xl" />
      <div
        className="absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "linear-gradient(to right, rgba(63,63,70,0.14) 1px, transparent 1px), linear-gradient(to bottom, rgba(63,63,70,0.14) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage:
            "radial-gradient(ellipse 80% 60% at 50% 0%, black 40%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 80% 60% at 50% 0%, black 40%, transparent 100%)",
        }}
      />
    </div>
  );
}

function LogoMark() {
  return (
    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#1D9E75]/15 border border-[#1D9E75]/35">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
        <path
          d="M4 6h16M4 12h10M4 18h13"
          stroke="#34d399"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <circle cx="19" cy="12" r="2" fill="#34d399" />
      </svg>
    </span>
  );
}

function SourceDot() {
  return <span className="h-1.5 w-1.5 rounded-full bg-zinc-700" />;
}

function ArrowRight() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path
        d="M5 12h14m0 0-6-6m6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path
        d="M20 6 9 17l-5-5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconBolt() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path
        d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconAgent() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <rect x="5" y="7" width="14" height="12" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 7V3m0 0h-2m2 0h2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="9.5" cy="12.5" r="1.2" fill="currentColor" />
      <circle cx="14.5" cy="12.5" r="1.2" fill="currentColor" />
      <path d="M9.5 16h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function IconSearch() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
      <path d="m20 20-3.8-3.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function IconSync() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path
        d="M20 11a8 8 0 0 0-14.9-3M4 13a8 8 0 0 0 14.9 3"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <path d="M4 4v4h4M20 20v-4h-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconSlack() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path
        d="M8 3h8a2 2 0 0 1 2 2v3M8 21h8m-8 0a2 2 0 0 1-2-2v-3m2 5h8a2 2 0 0 0 2-2v-3M6 8H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h2m12-8h2a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="2.4" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function IconApi() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path
        d="m8 8-4 4 4 4m8-8 4 4-4 4m-2.5-11-3 14"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
