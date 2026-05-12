"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Menu, X, ArrowUpRight, Sparkles } from "lucide-react";
// ThemeToggle deliberately NOT imported.  Marketing pages render in a
// fixed warm-graphite dark register so the brand impression is always
// deliberate (Citadel / Two Sigma / Renaissance pattern).  The toggle
// still lives in the console nav where operators have legitimate
// light-mode preferences.

/**
 * AYVID MarketingNav — Polo-style floating pill on warm graphite + gold.
 *
 * Restored to the original architecture (a single rounded pill floating
 * inside container-page) so the marketing site reads as "premium fashion
 * house" rather than "editorial newspaper".  The brief's masthead idea
 * lives elsewhere (the console overview header still uses it), but the
 * top-of-funnel marketing nav is unambiguously a Polo pill.
 *
 * Tokens still come from the warm-graphite + gold foundation, so the
 * pill renders with deep ink-on-ink restraint in dark mode and warm
 * cream-on-paper restraint in light mode.
 */

const NAV: { href: string; label: string }[] = [
  { href: "/approach", label: "Approach" },
  { href: "/research", label: "Research" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export function MarketingNav() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [open]);

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-40">
      <div
        className="container-page pointer-events-none flex justify-center"
        style={{ paddingTop: "var(--s-4)" }}
      >
        <div
          className={clsx(
            "nav-floating pointer-events-auto flex items-center",
            (scrolled || open) && "is-scrolled",
          )}
          style={{ gap: "var(--s-3)" }}
        >
          {/* Brand mark — monogram + wordmark, Polo style. */}
          <Link
            href="/"
            className="group flex items-center gap-2.5 rounded-full px-1.5 py-1 sm:px-2"
          >
            <span
              className="grid h-8 w-8 place-items-center rounded-full bg-[hsl(var(--fg))] text-[hsl(var(--bg))] transition-transform group-hover:rotate-[8deg]"
              aria-hidden
            >
              <Sparkles className="h-3.5 w-3.5" strokeWidth={2.4} />
            </span>
            <span className="text-[15.5px] font-semibold tracking-tight text-fg">
              AYVID
            </span>
          </Link>

          {/* Center desktop nav — pushed right */}
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            {NAV.map(({ href, label }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  className={clsx(
                    "rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors",
                    active
                      ? "bg-[hsl(var(--bg-sunken))] text-fg"
                      : "text-muted hover:text-fg",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2 md:ml-0">
            <Link
              href="/console"
              className="hidden items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12px] font-semibold uppercase tracking-[0.14em] transition-colors sm:inline-flex"
              style={{
                color: "hsl(var(--brand))",
                border: "1px solid hsl(var(--brand-dim))",
                background: "transparent",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "hsl(var(--brand))";
                e.currentTarget.style.color = "hsl(var(--brand-fg))";
                e.currentTarget.style.borderColor = "hsl(var(--brand))";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "hsl(var(--brand))";
                e.currentTarget.style.borderColor = "hsl(var(--brand-dim))";
              }}
            >
              Console <ArrowUpRight className="h-3 w-3" />
            </Link>
            <button
              type="button"
              aria-label={open ? "Close menu" : "Open menu"}
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="grid h-9 w-9 place-items-center rounded-full border border-app bg-panel text-fg md:hidden"
            >
              {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile sheet — same Polo language, full panel, single hairline. */}
      <div
        className={clsx(
          "pointer-events-auto md:hidden",
          open ? "block" : "hidden",
        )}
      >
        <div className="container-page mt-3">
          <div
            className="rounded-2xl border border-app bg-panel px-5 py-6"
            style={{ boxShadow: "0 0 0 1px var(--rule)" }}
          >
            <p className="acronym-band mb-5">
              <b>A</b>symmetric&nbsp;<b>Y</b>ield&nbsp;<b>V</b>ia&nbsp;
              <b>I</b>ntelligent&nbsp;<b>D</b>ecisions
            </p>
            <ul className="divide-y divide-[hsl(var(--border))]">
              {NAV.map(({ href, label }) => {
                const active = pathname === href;
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      className={clsx(
                        "flex items-center justify-between py-3 text-[20px] font-semibold tracking-tight",
                        active ? "text-fg" : "text-fg/80",
                      )}
                    >
                      {label}
                      <ArrowUpRight className="h-4 w-4 text-muted" />
                    </Link>
                  </li>
                );
              })}
              <li>
                <Link
                  href="/console"
                  className="mt-3 flex items-center justify-between rounded-full px-4 py-3 text-[14px] font-semibold"
                  style={{
                    background: "hsl(var(--brand))",
                    color: "hsl(var(--brand-fg))",
                  }}
                >
                  Operations console
                  <ArrowUpRight className="h-4 w-4" />
                </Link>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </header>
  );
}
