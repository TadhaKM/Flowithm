// Redirect to /setup the first time a user lands without a flowithm_org_id
// cookie. Self-hosted single-tenant deploys can skip this gate by setting
// FLOWITHM_DEFAULT_ORG_ID in the dashboard's environment — middleware
// treats that as "we already have an org, no setup needed".
//
// Routes that bypass the redirect:
//   /setup itself, /api/* (proxy + setup endpoints), /_next/*, favicon
import { NextRequest, NextResponse } from "next/server";

const HAS_DEFAULT_ORG = !!process.env.FLOWITHM_DEFAULT_ORG_ID;
const COOKIE = "flowithm_org_id";

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Bypass: setup page, the post-setup onboarding pages, API routes,
  // and Next internals. Onboarding pages still require the org cookie
  // (they're step 2/3 of the flow) — that gate is enforced below by
  // looking for the cookie on every non-bypassed path.
  if (
    pathname.startsWith("/setup")
    || pathname.startsWith("/onboarding/")
    || pathname.startsWith("/api/")
    || pathname.startsWith("/_next/")
    || pathname === "/favicon.ico"
  ) {
    // /onboarding/* still needs the org cookie. If absent, fall back
    // to /setup so the user starts at step 1.
    if (pathname.startsWith("/onboarding/") && !req.cookies.get(COOKIE) && !HAS_DEFAULT_ORG) {
      const url = req.nextUrl.clone();
      url.pathname = "/setup";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  if (HAS_DEFAULT_ORG) return NextResponse.next();
  if (req.cookies.get(COOKIE)) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = "/setup";
  return NextResponse.redirect(url);
}

// Don't run on static assets or images — Next handles them anyway.
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
