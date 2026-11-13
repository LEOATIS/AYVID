import { NextResponse, type NextRequest } from "next/server";
import { ACCESS_COOKIE, ACCESS_MAX_AGE_SEC, ACCESS_PAGE, accessToken, safeNext } from "@/lib/access";

const FAIL_DELAY_MS = 800; // slows password guessing

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const next = safeNext(String(form.get("next") ?? ""));
  const given = String(form.get("password") ?? "");
  const password = process.env.ACCESS_PASSWORD;
  // 303 so the browser follows with GET after the form POST.
  if (password && given && (await accessToken(given)) === (await accessToken(password))) {
    const res = NextResponse.redirect(new URL(next, req.url), 303);
    res.cookies.set(ACCESS_COOKIE, await accessToken(password), {
      httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/", maxAge: ACCESS_MAX_AGE_SEC,
    });
    return res;
  }
  await new Promise((r) => setTimeout(r, FAIL_DELAY_MS));
  return NextResponse.redirect(new URL(`${ACCESS_PAGE}?error=1&next=${encodeURIComponent(next)}`, req.url), 303);
}
