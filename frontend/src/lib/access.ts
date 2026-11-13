/**
 * Internal-access gate shared by middleware (Edge) and the login route (Node).
 * The password lives only in the server env var ACCESS_PASSWORD; the cookie holds an
 * HMAC of it, never the password itself. Unset password = everything stays locked.
 */
export const ACCESS_COOKIE = "ayvid_access";
export const ACCESS_PAGE = "/internal";
export const ACCESS_MAX_AGE_SEC = 60 * 60 * 24 * 30;

export const PROTECTED_PREFIXES = ["/research", "/companies", "/theses", "/screener", "/watchlists", "/console"];

export function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Token stored in the cookie: HMAC-SHA256(password, fixed label), hex. */
export async function accessToken(password: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(password), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode("ayvid-internal-access-v1"));
  return Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Only same-site paths survive the round trip through the login form (blocks open redirects). */
export function safeNext(next: string | null | undefined): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/research";
}
