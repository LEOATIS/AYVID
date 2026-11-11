"use client";
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowRight, FlaskConical } from "lucide-react";

const STORAGE_KEY = "ayvid-console-unlocked";

/**
 * Interstitial in front of the Quant Lab (historical shock backtests, paper only).
 * Not a security boundary — the API enforces access. It exists so visitors know
 * they are leaving the research workspace for a backtest lab, and `?demo=1`
 * (used by every in-app link) skips it.
 */
export function ConsoleGate({ children }: { children: ReactNode }) {
  const [unlocked, setUnlocked] = useState<boolean | null>(null);

  const enter = () => {
    try {
      sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // storage blocked (private mode) — still let them in for this view
    }
    setUnlocked(true);
  };

  useEffect(() => {
    let ok = false;
    try {
      ok = sessionStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      ok = false;
    }
    if (!ok && new URLSearchParams(window.location.search).get("demo") === "1") {
      enter();
      return;
    }
    setUnlocked(ok);
  }, []);

  if (unlocked === null) {
    return <div className="grid min-h-[60vh] place-items-center text-[12px] text-subtle">Loading…</div>;
  }
  if (unlocked) return <>{children}</>;

  return (
    <div className="relative flex min-h-[100dvh] flex-col bg-app">
      <header className="container-page flex h-16 items-center justify-between">
        <Link href="/" aria-label="AYVID home" className="flex items-center">
          <img src="/brand/wordmark-horizontal-dark.svg" alt="AYVID" width={96} height={24} style={{ height: 24, width: "auto" }} />
        </Link>
        <Link href="/research" className="text-[11px] uppercase tracking-[0.18em] text-subtle hover:text-fg">
          ← Research workspace
        </Link>
      </header>

      <main className="container-page mt-[8vh] max-w-[720px] pb-24">
        <div className="eyebrow inline-flex items-center gap-2 text-[10px]">
          <FlaskConical className="h-3 w-3" aria-hidden /> Quant Lab · paper only
        </div>
        <h1 className="font-display display-2 mt-4 text-fg" style={{ textWrap: "balance" }}>
          Historical shock backtests behind the research workspace.
        </h1>
        <p className="lede mt-6">
          The lab replays named historical shocks (2018–2025) through the supply-chain graph to check that the
          exposures the workspace surfaces behave as expected. Results are simulated with modeled costs. Nothing here
          places orders: paper trading is enforced and the research workspace has no path to execution.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <button type="button" onClick={enter} className="btn-primary" autoFocus>
            Enter Shock Lab <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </button>
          <Link href="/research" className="btn-ghost">Back to research</Link>
        </div>
        <p className="mt-3 text-[11px] text-subtle">Hypothetical, backtested results. Not investment advice.</p>
      </main>

      <footer className="container-page mt-auto py-8 text-[11px] text-subtle">
        © {new Date().getFullYear()} AYVID · Research only · Not an offer or recommendation
      </footer>
    </div>
  );
}
