import type { ReactNode } from "react";
import Link from "next/link";
import { ConsoleGate } from "@/components/console/ConsoleGate";
import { ConsoleNav } from "@/components/layout/ConsoleNav";

export const metadata = {
  title: "Shock Lab",
  robots: { index: false, follow: false, nocache: true },
};

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <ConsoleGate>
      <div className="console-shell flex min-h-screen flex-col">
        <div className="border-b border-app bg-[hsl(var(--bg-sunken))]">
          <div className="mx-auto flex w-full max-w-[1480px] flex-wrap items-center justify-between gap-2 px-6 py-2 text-[12px]">
            <p className="text-muted">
              <span className="font-medium text-fg">Quant Lab</span> · historical shock backtests behind the research
              workspace · paper only, hypothetical results
            </p>
            <Link href="/research" className="link-arrow">← Research workspace</Link>
          </div>
        </div>
        <ConsoleNav />
        <main className="mx-auto w-full max-w-[1480px] px-6 py-8">{children}</main>
        <footer className="mx-auto mt-8 w-full max-w-[1480px] border-t border-app px-6 py-6">
          <div className="flex flex-col items-start justify-between gap-2 sm:flex-row sm:items-center">
            <p className="text-[11px] text-subtle">
              AYVID · Quant Lab · Hypothetical backtests, not investment advice
            </p>
            <p className="text-[11px] text-subtle">
              Tier 1 · sub-100ms · Tier 3 ~30s shock override · IBKR Tiered post-cost
            </p>
          </div>
        </footer>
      </div>
    </ConsoleGate>
  );
}
