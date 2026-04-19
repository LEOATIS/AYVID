import type { ReactNode } from "react";
import { ConsoleGate } from "@/components/console/ConsoleGate";
import { ConsoleNav } from "@/components/layout/ConsoleNav";

export const metadata = {
  title: "Operations console",
  robots: { index: false, follow: false, nocache: true },
};

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <ConsoleGate>
      <div className="console-shell flex min-h-screen flex-col">
        <ConsoleNav />
        <main className="mx-auto w-full max-w-[1480px] px-6 py-8">{children}</main>
        <footer className="mx-auto mt-8 w-full max-w-[1480px] border-t border-app px-6 py-6">
          <div className="flex flex-col items-start justify-between gap-2 sm:flex-row sm:items-center">
            <p className="text-[11px] text-subtle">
              AYVID · Operations console · Not for distribution
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
