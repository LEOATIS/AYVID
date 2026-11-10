"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import clsx from "clsx";
import { BookMarked, Building2, FlaskConical, ListChecks, Menu, Plus, Search, SlidersHorizontal, X } from "lucide-react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { HEALTH_LABEL, useMeta, useSessions, useTheses, type Health } from "@/lib/research";
import { EvidenceDrawer, EvidenceProvider } from "./EvidenceDrawer";

const NAV = [
  { href: "/research", label: "Research", icon: Search },
  { href: "/companies", label: "Companies", icon: Building2 },
  { href: "/theses", label: "Theses", icon: BookMarked },
  { href: "/screener", label: "Screener", icon: SlidersHorizontal },
  { href: "/watchlists", label: "Watchlists", icon: ListChecks },
  { href: "/console/live-shock", label: "Shock Lab", icon: FlaskConical },
];

export const FOCUS_QUERY_EVENT = "ayvid:focus-query";

export function HealthDot({ health }: { health: Health }) {
  const cls = health === "HEALTHY" ? "dot-live" : health === "TRIGGERED" ? "dot-down" : "dot-warn";
  return <span className={clsx("dot shrink-0", cls)} role="img" aria-label={HEALTH_LABEL[health]} />;
}

function Rail({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const theses = useTheses();
  const sessions = useSessions();
  const meta = useMeta();

  return (
    <nav aria-label="Workspace" className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pt-4 pb-3">
        <Link href="/" className="font-display text-[22px] font-semibold tracking-tight text-fg" onClick={onNavigate}>
          AYVID<span className="text-brand">.</span>
        </Link>
        <span className="label-mono">Research</span>
      </div>
      <div className="px-3">
        <Link href="/research" className="ws-btn-primary w-full justify-between" onClick={onNavigate}>
          <span className="inline-flex items-center gap-2"><Plus className="h-4 w-4" aria-hidden /> New research</span>
          <kbd className="ws-kbd">⌘K</kbd>
        </Link>
      </div>
      <ul className="mt-4 space-y-0.5 px-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || (href !== "/research" && pathname.startsWith(href)) || (href === "/research" && pathname.startsWith("/research"));
          return (
            <li key={href}>
              <Link href={href} onClick={onNavigate} aria-current={active ? "page" : undefined} className={clsx("ws-nav", active && "is-active")}>
                <Icon className="h-4 w-4" aria-hidden /> {label}
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="mt-5 min-h-0 flex-1 overflow-y-auto px-2">
        <p className="label-mono px-2 pb-1">Saved theses</p>
        <ul>
          {(theses.data ?? []).slice(0, 8).map((t) => (
            <li key={t.thesis_id}>
              <Link href={`/theses/${t.thesis_id}`} onClick={onNavigate} className="ws-nav text-[13px]">
                <HealthDot health={t.health} />
                <span className="truncate">{t.title}</span>
              </Link>
            </li>
          ))}
          {theses.data && theses.data.length === 0 ? <li className="px-2 py-1 text-[12px] text-subtle">None yet — save one from research.</li> : null}
          {theses.error ? <li className="px-2 py-1 text-[12px] text-neg">Could not load theses.</li> : null}
        </ul>
        <p className="label-mono px-2 pt-4 pb-1">Recent research</p>
        <ul>
          {(sessions.data ?? []).map((s) => (
            <li key={s.session_id}>
              <Link href={`/research/${s.session_id}`} onClick={onNavigate} className={clsx("ws-nav text-[13px]", pathname === `/research/${s.session_id}` && "is-active")}>
                <span className="truncate">{s.title ?? s.query}</span>
              </Link>
            </li>
          ))}
          {sessions.data && sessions.data.length === 0 ? <li className="px-2 py-1 text-[12px] text-subtle">No sessions yet.</li> : null}
        </ul>
      </div>

      <div className="border-t border-app px-4 py-3 text-[12px]">
        {meta.error ? (
          <p className="text-neg">Backend unreachable — start the API on :8000.</p>
        ) : (
          <>
            <p className="flex items-center gap-2 text-muted">
              <span className={clsx("dot", meta.data?.data.stale.is_stale ? "dot-warn" : "dot-live")} aria-hidden />
              <span>{meta.data ? meta.data.data.mode_label : "Loading…"}</span>
            </p>
            <p className="mt-1 text-subtle">
              Engine:{" "}
              <span className="num text-muted">
                {meta.data ? (meta.data.llm.provider === "deterministic" ? "Deterministic" : `${meta.data.llm.provider} · ${meta.data.llm.model}`) : "…"}
              </span>
            </p>
            {meta.data?.data.stale.is_stale ? (
              <p className="mt-1 text-warn">Local bars end {meta.data.data.latest_bar}</p>
            ) : null}
          </>
        )}
        <div className="mt-3 flex items-center justify-between">
          <span className="text-subtle">Paper-only · no orders</span>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const [railOpen, setRailOpen] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const sheetClose = useRef<HTMLButtonElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sheetWasOpen = useRef(false);

  useEffect(() => {
    if (railOpen) sheetClose.current?.focus();
    else if (sheetWasOpen.current) menuButton.current?.focus();
    sheetWasOpen.current = railOpen;
  }, [railOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, select, [contenteditable='true']") != null;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
      } else {
        if (e.key === "Escape") setRailOpen(false);
        return;
      }
      if (pathname.startsWith("/research")) window.dispatchEvent(new Event(FOCUS_QUERY_EVENT));
      else router.push("/research");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pathname, router]);

  return (
    <EvidenceProvider>
      <div className="console-shell ws-root">
        <a href="#ws-main" className="ws-skip">Skip to content</a>
        <aside className="ws-rail" aria-label="Workspace navigation" inert={railOpen}>
          <Rail />
        </aside>
        {railOpen ? (
          <div className="ws-sheet-backdrop" onClick={() => setRailOpen(false)} aria-hidden />
        ) : null}
        <div role="dialog" aria-modal="true" aria-label="Navigation" className={clsx("ws-rail-sheet", railOpen && "is-open")} hidden={!railOpen}>
          <button ref={sheetClose} type="button" className="ws-icon-btn absolute right-3 top-3" aria-label="Close navigation" onClick={() => setRailOpen(false)}>
            <X className="h-4 w-4" aria-hidden />
          </button>
          <Rail onNavigate={() => setRailOpen(false)} />
        </div>
        <div className="ws-center" inert={railOpen}>
          <header className="ws-mobilebar">
            <button ref={menuButton} type="button" className="ws-icon-btn" aria-label="Open navigation" aria-expanded={railOpen} onClick={() => setRailOpen(true)}>
              <Menu className="h-5 w-5" aria-hidden />
            </button>
            <Link href="/" className="font-display text-[18px] font-semibold text-fg">AYVID<span className="text-brand">.</span></Link>
            <Link href="/research" className="ws-icon-btn" aria-label="New research"><Plus className="h-5 w-5" aria-hidden /></Link>
          </header>
          <main id="ws-main" className="ws-main">{children}</main>
        </div>
        <EvidenceDrawer />
      </div>
    </EvidenceProvider>
  );
}
