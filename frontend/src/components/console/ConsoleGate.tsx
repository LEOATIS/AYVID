"use client";
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowRight, Lock } from "lucide-react";

const STORAGE_KEY = "ayvid-console-unlocked";
const REQUIRED_PASSPHRASE =
  process.env.NEXT_PUBLIC_CONSOLE_PASSPHRASE || "ayvid";

/**
 * Wraps the operations console in a soft access gate.
 * This is symbolic, not a security boundary — the heavy lifting still
 * lives behind the FastAPI service. The gate exists so the public site
 * can advertise an "internal access only" surface without exposing
 * P&L, positions, and signals on first paint.
 */
export function ConsoleGate({ children }: { children: ReactNode }) {
  const [unlocked, setUnlocked] = useState<boolean | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const ok = sessionStorage.getItem(STORAGE_KEY) === "1";
      setUnlocked(ok);
    } catch {
      setUnlocked(false);
    }
  }, []);

  if (unlocked === null) {
    return <div className="grid min-h-[60vh] place-items-center text-[12px] text-subtle">…</div>;
  }
  if (unlocked) return <>{children}</>;

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim().toLowerCase() === REQUIRED_PASSPHRASE.toLowerCase()) {
      sessionStorage.setItem(STORAGE_KEY, "1");
      setUnlocked(true);
      setError(null);
    } else {
      setError("That passphrase isn't recognised. Contact the desk.");
    }
  };

  return (
    <div className="relative min-h-[100dvh] overflow-hidden bg-app">
      {/* faint grid */}
      <div className="pointer-events-none absolute inset-0 marketing-grid opacity-30" aria-hidden />

      <header className="container-page flex h-16 items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center bg-ink text-[hsl(var(--bg))]">
            <span className="font-display text-[14px] leading-none">A</span>
          </span>
          <span className="font-display text-[18px] tracking-tight text-fg">AYVID</span>
        </Link>
        <Link
          href="/"
          className="text-[11px] uppercase tracking-[0.18em] text-subtle hover:text-fg"
        >
          ← Back to ayvid.com
        </Link>
      </header>

      <main className="container-page mt-[6vh] grid grid-cols-1 gap-12 pb-24 md:mt-[12vh] md:grid-cols-12">
        <section className="md:col-span-7 reveal">
          <div className="eyebrow inline-flex items-center gap-2 text-[10px]">
            <Lock className="h-3 w-3" /> Restricted area
          </div>
          <h1 className="font-display display-1 mt-4 text-fg">
            Operations console.
            <br />
            <span className="text-muted">Internal access only.</span>
          </h1>
          <p className="lede mt-6">
            Beyond this page lies the live trading surface — equity curves, open positions,
            the active signal book, the shock catalog, and the demo replay. We don&rsquo;t
            publish that to the open internet, the same way Renaissance, Jane Street, and
            Citadel don&rsquo;t.
          </p>
          <p className="prose-body mt-4 max-w-prose">
            If you&rsquo;re from YC, an LP, or otherwise expected, you&rsquo;ve already
            been given the passphrase. Otherwise, the public materials at{" "}
            <Link href="/" className="link-arrow">ayvid.com</Link> describe everything we
            disclose externally.
          </p>
        </section>

        <section className="md:col-span-5">
          <div className="surface bg-panel p-8">
            <div className="eyebrow text-[10px]">Sign in</div>
            <h2 className="font-display display-3 mt-2 text-fg">Enter passphrase</h2>
            <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-3">
              <input
                type="password"
                autoFocus
                autoComplete="current-password"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="••••••••"
                className="w-full border border-strong bg-app px-4 py-3 font-mono text-[14px] text-fg outline-none transition focus:border-[hsl(var(--fg))]"
              />
              {error && (
                <p className="text-[12px] text-neg">{error}</p>
              )}
              <button type="submit" className="btn-primary w-full justify-center">
                Continue <ArrowRight className="h-3.5 w-3.5" />
              </button>
              <p className="mt-1 text-[11px] text-subtle">
                Session-scoped unlock. Closing the tab logs you back out.
              </p>
            </form>
          </div>
        </section>
      </main>

      <footer className="container-page mt-auto py-8 text-[11px] text-subtle">
        © {new Date().getFullYear()} AYVID · For informational purposes only · Not an offer
      </footer>
    </div>
  );
}
