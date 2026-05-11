"use client";

// Shared top nav. All four routes (home, knowledge base, agent API, sources)
// always render the full set of links so users can hop between them from any
// page. The current route is rendered as a non-clickable span; everything
// else is a Link.

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

type Item = { label: string; href: string };

const ITEMS: Item[] = [
  { label: "Flowithm", href: "/" },
  { label: "Knowledge base", href: "/brain" },
  { label: "Agent API", href: "/brain/api" },
  { label: "Sources", href: "/brain/sources" },
];

export function TopNav({ showSignOut = true }: { showSignOut?: boolean }) {
  const pathname = usePathname() || "/";
  const router = useRouter();

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <header className="mb-12 flex items-center justify-between gap-4">
      <div className="flex items-center gap-6">
        {ITEMS.map((item, i) => {
          const active = pathname === item.href;
          const isBrand = i === 0;
          const baseClass = isBrand
            ? "text-base font-medium tracking-tight"
            : "text-sm";
          if (active) {
            return (
              <span key={item.href} className={`${baseClass} text-zinc-100 font-medium`}>
                {item.label}
              </span>
            );
          }
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`${baseClass} ${isBrand ? "text-zinc-100" : "text-zinc-500"} hover:text-zinc-300 transition-colors`}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
      {showSignOut && (
        <button
          onClick={signOut}
          className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Sign out
        </button>
      )}
    </header>
  );
}
