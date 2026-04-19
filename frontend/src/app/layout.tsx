import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "AYVID — AI-Native Hedge Fund Operating System",
  description:
    "Live shock detection, supply-chain causal propagation, and execution-grade signal generation.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen grid-bg antialiased">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6 px-6 py-6">
          <header className="flex items-center justify-between border-b border-white/10 pb-4">
            <div className="flex items-center gap-3">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-blue-500/15 ring-1 ring-blue-400/30">
                <span className="text-sm font-bold tracking-tight text-blue-300">A</span>
              </span>
              <div>
                <div className="text-sm font-semibold tracking-tight">AYVID</div>
                <div className="text-[11px] uppercase tracking-[0.18em] text-white/40">
                  AI-Native Hedge Fund Operating System
                </div>
              </div>
            </div>
            <nav className="flex items-center gap-6 text-xs text-white/55">
              <span className="text-white/85">Live Shock</span>
              <span>Portfolio</span>
              <span>Signals</span>
              <span>Risk</span>
              <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-400/30">
                Paper
              </span>
            </nav>
          </header>
          {children}
          <footer className="mt-2 border-t border-white/5 pt-3 text-[11px] text-white/35">
            AYVID · Modular monolith · Tier 1 sub-100ms execution · Tier 3 ~30s
            shock override · IBKR Tiered post-cost
          </footer>
        </div>
      </body>
    </html>
  );
}
