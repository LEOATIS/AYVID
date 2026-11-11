"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Menu, X, ArrowUpRight } from "lucide-react";
import { Wordmark } from "@/components/ui/Wordmark";

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
  { href: "/research", label: "Workspace" },
  { href: "/notes", label: "Notes" },
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
          {/* Brand mark — the Golden-Gnomon SVG wordmark.  Marketing is
              pinned to the dark register (see (marketing)/layout.tsx),
              so the dark-variant SVG is always correct here.  The wordmark
              SVG already contains the mark + "AYVID" + tagline; we render
              it at 32px tall to fit the floating-pill geometry. */}
          <Link
            href="/"
            aria-label="AYVID home"
            className="flex items-center"
            style={{ padding: "4px 4px", textDecoration: "none" }}
          >
            <Wordmark height={32} alt="AYVID — Asymmetric Yield Via Intelligent Decisions" />
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
              href="/console/live-shock?demo=1"
              className="hidden items-center gap-1.5 rounded-full border border-[hsl(var(--brand-dim))] bg-transparent px-3.5 py-1.5 text-[12px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--brand))] transition-colors hover:border-[hsl(var(--brand))] hover:bg-[hsl(var(--brand))] hover:text-[hsl(var(--brand-fg))] focus-visible:bg-[hsl(var(--brand))] focus-visible:text-[hsl(var(--brand-fg))] sm:inline-flex"
            >
              Shock Lab <ArrowUpRight className="h-3 w-3" />
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
                  href="/console/live-shock?demo=1"
                  className="mt-3 flex items-center justify-between rounded-full px-4 py-3 text-[14px] font-semibold"
                  style={{
                    background: "hsl(var(--brand))",
                    color: "hsl(var(--brand-fg))",
                  }}
                >
                  Shock Lab (paper only)
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
