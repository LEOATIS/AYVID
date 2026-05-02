"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  LineChart,
  Menu,
  Network,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import { ThemeToggle } from "../ui/ThemeToggle";
import { useRunner } from "@/lib/hooks";

const NAV: { href: string; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { href: "/console", label: "Overview", icon: Activity },
  { href: "/console/live-shock", label: "Live Shock", icon: Zap },
  { href: "/console/portfolio", label: "Portfolio", icon: Wallet },
  { href: "/console/signals", label: "Signals", icon: LineChart },
  { href: "/console/backtests", label: "Backtests", icon: BarChart3 },
  { href: "/console/scenarios", label: "Scenarios", icon: Network },
  { href: "/console/docs", label: "Docs", icon: BookOpen },
];

export function ConsoleNav() {
  const pathname = usePathname();
  const { data: runner } = useRunner();
  const live = runner?.is_alive;
  const mode = runner?.mode ?? "—";
  const [open, setOpen] = useState(false);

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
    <header className="sticky top-0 z-40 border-b border-app bg-[hsl(var(--bg)/0.85)] backdrop-blur-md">
      <div className="mx-auto flex h-[60px] w-full max-w-[1480px] items-center justify-between gap-4 px-4 sm:gap-6 sm:px-6">
        <div className="flex min-w-0 items-center gap-5">
          <Link
            href="/console"
            className="group flex shrink-0 items-center gap-2.5"
          >
            <span className="grid h-9 w-9 place-items-center rounded-[3px] bg-ink text-[hsl(var(--bg))]">
              <span className="font-display text-[17px] font-medium leading-none">
                A
              </span>
            </span>
            <div className="leading-tight">
              <div className="font-display text-[16px] font-medium tracking-tight text-fg group-hover:text-brand">
                AYVID
              </div>
              <div className="text-[9.5px] font-semibold uppercase tracking-[0.22em] text-subtle">
                Operations · console
              </div>
            </div>
          </Link>

          <nav className="hidden items-center gap-0.5 lg:flex">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active =
                href === "/console"
                  ? pathname === "/console"
                  : pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={clsx(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium transition-colors",
                    active
                      ? "bg-[hsl(var(--bg-sunken))] text-fg"
                      : "text-muted hover:bg-[hsl(var(--bg-sunken))] hover:text-fg",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <div className="hidden items-center gap-2 rounded-md border border-app bg-panel px-2.5 py-1.5 md:flex">
            <span className={clsx("dot", live ? "dot-live pulse-live" : "dot-idle")} />
            <span className="text-[11px] font-medium text-fg">
              {live ? "Live" : "Idle"}
            </span>
            <span className="text-[11px] text-subtle">·</span>
            <span className="text-[11px] uppercase tracking-wider text-muted">
              {mode === "ib_paper" ? "Paper" : mode}
            </span>
          </div>
          <ThemeToggle />
          {/* Build SHA badge — surfaces deploy drift at a glance.
             If the displayed SHA doesn't match `git rev-parse --short HEAD`
             the new bundle hasn't reached this user's tab yet (CDN cache
             or sticky CF revision). The CI smoke test asserts the backend
             SHA matches commit, so a mismatch here ⇒ frontend stragglers,
             not bad deploys. */}
          {(() => {
            const sha = (process.env.NEXT_PUBLIC_BUILD_SHA || "dev").trim();
            const short = sha.length >= 7 ? sha.slice(0, 7) : sha;
            const isReal = sha !== "dev" && sha !== "unknown" && sha.length >= 7;
            const href = isReal
              ? `https://github.com/LEOATIS/AYVID/commit/${sha}`
              : undefined;
            const className =
              "hidden items-center gap-1 rounded-md border border-app bg-panel px-2 py-1 font-mono text-[10px] tracking-[0.06em] text-subtle hover:text-fg md:inline-flex";
            const inner = (
              <>
                <span className="text-muted/60">build</span>
                <span className="text-fg/70">{short}</span>
              </>
            );
            return href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                title={`Build SHA: ${sha}`}
                className={className}
              >
                {inner}
              </a>
            ) : (
              <span title={`Build SHA: ${sha}`} className={className}>
                {inner}
              </span>
            );
          })()}
          <Link
            href="/"
            className="hidden items-center gap-1 text-[11px] uppercase tracking-[0.16em] text-subtle hover:text-fg xl:inline-flex"
          >
            Public site <ArrowUpRight className="h-3 w-3" />
          </Link>
          <button
            type="button"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="grid h-9 w-9 place-items-center rounded-md border border-app bg-panel text-fg lg:hidden"
          >
            {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {/* Mobile / tablet sheet */}
      <div
        className={clsx(
          "lg:hidden",
          open
            ? "border-t border-app bg-[hsl(var(--bg))]"
            : "pointer-events-none h-0 overflow-hidden",
        )}
      >
        <div className="mx-auto w-full max-w-[1480px] px-4 py-5 sm:px-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-subtle">
              Sections
            </span>
            <span className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              <span className={clsx("dot", live ? "dot-live pulse-live" : "dot-idle")} />
              {live ? "Live" : "Idle"} · {mode === "ib_paper" ? "Paper" : mode}
            </span>
          </div>
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active =
                href === "/console"
                  ? pathname === "/console"
                  : pathname.startsWith(href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    className={clsx(
                      "flex items-center gap-2 rounded-md border px-3 py-2.5 text-[13px] font-medium",
                      active
                        ? "border-strong bg-[hsl(var(--bg-sunken))] text-fg"
                        : "border-app bg-panel text-muted hover:text-fg",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </header>
  );
}
