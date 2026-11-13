import Link from "next/link";
import type { Metadata } from "next";
import { Lock } from "lucide-react";
import { Wordmark } from "@/components/ui/Wordmark";
import { safeNext } from "@/lib/access";

export const metadata: Metadata = { title: "Internal access", robots: { index: false } };

export default async function InternalAccessPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  const target = safeNext(next);
  const area = target.startsWith("/console") ? "Shock Lab" : "research workspace";
  return (
    <div className="marketing-shell flex min-h-screen items-center justify-center px-4" data-theme="dark">
      <main className="w-full max-w-md">
        <Link href="/" aria-label="AYVID home" className="mb-10 inline-block">
          <Wordmark height={30} />
        </Link>
        <p className="label-mono mb-3 flex items-center justify-start gap-2 text-left text-brand">
          <Lock className="h-3.5 w-3.5" aria-hidden /> Internal access
        </p>
        <h1 className="font-display text-[30px] leading-tight text-fg">The {area} is for internal users.</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-muted">
          AYVID&apos;s research workspace and Shock Lab are open to our team and invited partners only.
          Enter the access password to continue.
        </p>
        <form method="post" action="/internal/login" className="mt-8 space-y-3">
          <input type="hidden" name="next" value={target} />
          <label htmlFor="password" className="block text-[13px] text-muted">Access password</label>
          <input
            id="password" name="password" type="password" required autoFocus autoComplete="current-password"
            aria-invalid={error ? true : undefined} aria-describedby={error ? "access-error" : undefined}
            className="w-full rounded-lg border border-app bg-panel px-3.5 py-2.5 text-[15px] text-fg outline-none focus-visible:border-[hsl(var(--brand))]"
          />
          {error ? (
            <p id="access-error" role="alert" className="text-[13px] text-neg">That password is not correct.</p>
          ) : null}
          <button
            type="submit"
            className="w-full rounded-lg bg-[hsl(var(--brand))] px-4 py-2.5 text-[14px] font-semibold text-[hsl(var(--brand-fg))] transition-opacity hover:opacity-90"
          >
            Continue
          </button>
        </form>
        <p className="mt-6 text-[13px] text-subtle">
          Want access? Write to <a href="mailto:people@ayvid.ai" className="text-muted underline">people@ayvid.ai</a>{" "}
          or <Link href="/" className="text-muted underline">return home</Link>.
        </p>
      </main>
    </div>
  );
}
