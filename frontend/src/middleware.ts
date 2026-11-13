import { NextResponse, type NextRequest } from "next/server";
import { ACCESS_COOKIE, ACCESS_PAGE, accessToken } from "@/lib/access";

/** Workspace and Shock Lab are internal-only: no valid access cookie → the internal-access page. */
export async function middleware(req: NextRequest) {
  const password = process.env.ACCESS_PASSWORD;
  const cookie = req.cookies.get(ACCESS_COOKIE)?.value;
  if (password && cookie && cookie === (await accessToken(password))) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = ACCESS_PAGE;
  url.search = `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(url);
}

// Keep in sync with PROTECTED_PREFIXES in lib/access.ts.
export const config = {
  matcher: ["/research/:path*", "/companies/:path*", "/theses/:path*", "/screener/:path*", "/watchlists/:path*", "/console/:path*"],
};
