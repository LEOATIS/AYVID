import type { ReactNode } from "react";
import { MarketingNav } from "@/components/marketing/MarketingNav";
import { MarketingFooter } from "@/components/marketing/MarketingFooter";

/**
 * Marketing route group — fixed warm-graphite + gold register.
 *
 * `data-theme="dark"` is hardcoded on the shell so the marketing site
 * always presents in the deliberate brand register, regardless of the
 * visitor's OS preference or any previous theme-toggle state.
 *
 * This matches the pattern every serious finance site uses — Citadel,
 * Two Sigma, Renaissance, Bridgewater all anchor their public face to
 * one fixed aesthetic so visitors never see a brand-on / brand-off
 * variant.  The console (operations control surface) remains theme-
 * toggleable because operators have legitimate light-mode preferences.
 *
 * The marketing-shell CSS in globals.css already keys the warm-graphite
 * token block on `[data-theme="dark"]`, so this single attribute pins
 * the whole sub-tree.
 */
export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div
      className="marketing-shell flex min-h-screen flex-col"
      data-theme="dark"
    >
      <MarketingNav />
      <main className="flex-1 pt-[88px] sm:pt-[100px]">{children}</main>
      <MarketingFooter />
    </div>
  );
}
