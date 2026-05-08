// Supabase Auth session gate. Refreshes the session token on every
// request (keeps cookies fresh) and redirects unauthenticated visitors
// to /login for any protected route.
//
// Public paths (no session required):
//   /login, /signup, /api/*, /_next/*, /favicon.ico
//
// Everything else — including /brain/*, /setup, /onboarding/*, / — needs
// a valid Supabase Auth session.
import { createServerClient } from "@supabase/ssr";
import { NextRequest, NextResponse } from "next/server";

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Bypass: auth pages, API routes, Next internals.
  if (
    pathname.startsWith("/login")
    || pathname.startsWith("/signup")
    || pathname.startsWith("/auth/")
    || pathname.startsWith("/api/")
    || pathname.startsWith("/_next/")
    || pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  // Create a Supabase client that reads/writes session cookies on the
  // request/response pair. The setAll callback updates both so the
  // refreshed token propagates to the browser.
  let response = NextResponse.next({ request: req });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return req.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            req.cookies.set(name, value),
          );
          response = NextResponse.next({ request: req });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // getUser() validates the JWT with the Supabase auth server — never
  // trust getSession() alone for gate checks since the JWT could be
  // tampered with client-side.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
