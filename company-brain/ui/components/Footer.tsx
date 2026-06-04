// Site-wide footer. Mounted once in app/layout.tsx so every page picks it
// up. Intentionally minimal — brand on the left, the legal/security link
// on the right, no menu sprawl.
import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-16 border-t border-zinc-800/80">
      <div className="max-w-6xl mx-auto px-6 py-6 flex flex-col gap-3 text-xs text-zinc-500 sm:flex-row sm:items-center sm:justify-between">
        <span>Flowithm</span>
        <Link
          href="/privacy"
          className="text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          Privacy &amp; security
        </Link>
      </div>
    </footer>
  );
}
