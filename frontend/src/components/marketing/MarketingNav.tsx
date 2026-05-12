"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { ThemeToggle } from "../ui/ThemeToggle";

/**
 * AYVID MarketingNav — newspaper masthead, per brief §5.1.
 *
 * Reference: anthropic.com header + a private-bank quarterly masthead.
 *
 *  • Full-width, sticky top, height 56px.
 *  • No floating pill, no backdrop-filter, no shadow.
 *  • On scroll > 40px: a single 1px hairline appears at the bottom edge.
 *    That is the ONLY scrolled-state change.
 *  • Wordmark left in Fraunces 600 · 18px · tracking -0.02em.
 *  • Nav links right-aligned, Inter 13/medium, 32px spacing, var(--fg-2)
 *    rest → var(--fg) hover.
 *  • "Console" CTA: gold text on transparent, 1px var(--brand-dim)
 *    border.  Hover fills gold with ink text.
 *
 * Mobile: same masthead, drawer expands inline (no rounded sheet, no
 * gradients, no backdrop blur).  The brief is explicit: "Mobile =
 * stacked masthead, same rules."
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

  // 40px scroll threshold is the brief's spec — looser than the previous
  // 8px so the hairline only appears once content has clearly moved
  // beyond the masthead, not on the slightest wheel nudge.
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
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
    <header
      className={clsx(
        "fixed inset-x-0 top-0 z-40 transition-colors duration-200",
        scrolled || open
          ? "border-b border-[color:var(--rule)]"
          : "border-b border-transparent",
      )}
      style={{
        background: "hsl(var(--bg))",
        height: 56,
        // var(--ease-monument) for the border-color transition; we set
        // it as inline style so the Tailwind `transition-colors` picks
        // up the right curve without a custom utility.
        transitionTimingFunction: "var(--ease-monument)",
      }}
    >
      <div
        className="container-page mx-auto flex h-full items-center"
        style={{ gap: 32 }}
      >
        {/* Wordmark — no monogram, no bracket; Fraunces 600 / 18px.
            Sets letter-spacing -0.02em per brief. */}
        <Link
          href="/"
          className="flex items-center"
          style={{
            fontFamily: 'var(--font-display), "Fraunces", Georgia, serif',
            fontWeight: 600,
            fontSize: 18,
            letterSpacing: "-0.02em",
            color: "hsl(var(--fg))",
          }}
        >
          AYVID
        </Link>

        {/* Desktop nav — right-aligned, Inter 13 medium, tracking 0.04em.
            Active state: same color as default text (var(--fg)); inactive
            sits at var(--fg-2) and lifts to var(--fg) on hover. */}
        <nav className="ml-auto hidden items-center md:flex" style={{ gap: 32 }}>
          {NAV.map(({ href, label }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className="transition-colors"
                style={{
                  fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
                  fontSize: 13,
                  fontWeight: 500,
                  letterSpacing: "0.04em",
                  color: active ? "hsl(var(--fg))" : "hsl(var(--fg-muted))",
                  transitionDuration: "var(--t-quick)",
                  transitionTimingFunction: "var(--ease-monument)",
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.color = "hsl(var(--fg))";
                }}
                onMouseLeave={(e) => {
                  if (!active)
                    e.currentTarget.style.color = "hsl(var(--fg-muted))";
                }}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3 md:ml-0">
          <ThemeToggle />
          {/* Console CTA — brief §5.1: gold text on transparent, 1px
              var(--brand-dim) border. Hover fills gold with ink text.
              No "Open Console →" anymore — the brief just calls it
              "Open Console"; ArrowUpRight icon removed for restraint. */}
          <Link
            href="/console"
            className="hidden sm:inline-flex items-center transition-colors"
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: "0.04em",
              padding: "8px 16px",
              borderRadius: 4,
              border: "1px solid hsl(var(--brand-dim))",
              color: "hsl(var(--brand))",
              background: "transparent",
              transitionDuration: "var(--t-quick)",
              transitionTimingFunction: "var(--ease-monument)",
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
            Open Console
          </Link>

          <button
            type="button"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="grid h-9 w-9 place-items-center transition-colors md:hidden"
            style={{
              border: "1px solid var(--rule-bold)",
              borderRadius: 4,
              color: "hsl(var(--fg))",
              background: "transparent",
              transitionDuration: "var(--t-quick)",
              transitionTimingFunction: "var(--ease-monument)",
            }}
          >
            {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {/* Mobile drawer — flush against masthead, no rounded sheet, no
          backdrop blur.  Just the same masthead vocabulary applied
          vertically. */}
      <div
        className={clsx("md:hidden", open ? "block" : "hidden")}
        style={{
          background: "hsl(var(--bg))",
          borderTop: "1px solid var(--rule)",
          borderBottom: "1px solid var(--rule)",
        }}
      >
        <div className="container-page mx-auto" style={{ paddingTop: 24, paddingBottom: 32 }}>
          <p
            style={{
              fontFamily: 'var(--font-mono), "JetBrains Mono", ui-monospace, monospace',
              fontSize: 10,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: "hsl(var(--fg-subtle))",
              marginBottom: 20,
            }}
          >
            Asymmetric Yield Via Intelligent Decisions
          </p>
          <ul style={{ display: "flex", flexDirection: "column", gap: 0 }}>
            {NAV.map(({ href, label }) => {
              const active = pathname === href;
              return (
                <li
                  key={href}
                  style={{ borderBottom: "1px solid var(--rule)" }}
                >
                  <Link
                    href={href}
                    className="flex items-center justify-between"
                    style={{
                      padding: "16px 0",
                      fontFamily:
                        'var(--font-display), "Fraunces", Georgia, serif',
                      fontSize: 22,
                      fontWeight: 600,
                      letterSpacing: "-0.022em",
                      color: active ? "hsl(var(--fg))" : "hsl(var(--fg-muted))",
                    }}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
          <Link
            href="/console"
            className="btn-paper-primary mt-6 inline-flex w-full justify-center"
          >
            Open Console
          </Link>
        </div>
      </div>
    </header>
  );
}
