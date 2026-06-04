// Public-facing privacy + security page. Plain-prose, no legal jargon —
// readers should be able to skim the seven sections in under two minutes.
// Linked from the site-wide footer (components/Footer.tsx).
import Link from "next/link";

export const metadata = {
  title: "Privacy & security — Flowithm",
  description: "How Flowithm handles your company's data.",
};

const SECTIONS: { heading: string; body: React.ReactNode }[] = [
  {
    heading: "Data isolation",
    body: (
      <>
        Every company&apos;s data is completely isolated. Your Slack threads,
        Notion pages, and workflows are never accessible to other organisations.
        Each company operates in its own namespace, with row-level security
        enforced at the database level.
      </>
    ),
  },
  {
    heading: "We never train on your data",
    body: (
      <>
        Flowithm uses large language models to extract and structure workflows.
        Your company data is never used to train any AI model — it is processed
        to generate your workflows and nothing else.
      </>
    ),
  },
  {
    heading: "Data encryption",
    body: (
      <>
        All data is encrypted in transit using TLS 1.2 or higher. All data is
        encrypted at rest using AES-256. Third-party tokens and credentials
        stored for source connections are additionally encrypted with
        AES-256-GCM before being written to the database.
      </>
    ),
  },
  {
    heading: "Access controls",
    body: (
      <>
        Your workflows and knowledge base are only accessible via authenticated
        API keys that you generate and control. Keys can be revoked at any time
        from the dashboard. Every API request is logged with a timestamp.
      </>
    ),
  },
  {
    heading: "Source permissions",
    body: (
      <>
        Flowithm only reads from sources you explicitly connect, and only
        accesses the channels and pages you specify. It never accesses content
        outside the scope you define.
      </>
    ),
  },
  {
    heading: "Data deletion",
    body: (
      <>
        You can delete your entire knowledge base at any time from the dashboard
        settings. On deletion, all workflows, chunks, embeddings, and source
        configurations are permanently removed within 24 hours.
      </>
    ),
  },
  {
    heading: "Contact",
    body: (
      <>
        For security questions or concerns, email{" "}
        <a
          href="mailto:jpau7400@gmail.com"
          className="text-[#1D9E75] hover:text-[#34b88a] transition-colors"
        >
          jpau7400@gmail.com
        </a>
        .
      </>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <main className="min-h-screen">
      <div className="max-w-3xl mx-auto px-6 py-12">
        <Link
          href="/"
          className="text-sm text-zinc-500 hover:text-zinc-200 transition-colors"
        >
          ← Flowithm
        </Link>

        <header className="mt-8 mb-10">
          <h1 className="text-3xl font-medium tracking-tight text-zinc-100">
            Privacy &amp; security
          </h1>
          <p className="mt-2 text-sm text-zinc-500">
            How Flowithm handles your company&apos;s data.
          </p>
        </header>

        <div className="space-y-10">
          {SECTIONS.map((section, i) => (
            <section key={section.heading}>
              <div className="mb-2 flex items-baseline gap-3">
                <span className="text-xs tabular-nums text-zinc-600">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <h2 className="text-base font-medium text-zinc-100">
                  {section.heading}
                </h2>
              </div>
              <p className="ml-9 text-sm leading-relaxed text-zinc-400">
                {section.body}
              </p>
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}
