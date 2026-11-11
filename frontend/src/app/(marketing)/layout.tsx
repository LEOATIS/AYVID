import type { ReactNode } from "react";
import { MarketingNav } from "@/components/marketing/MarketingNav";
import { MarketingFooter } from "@/components/marketing/MarketingFooter";

/**
 * Marketing route group: pinned to the dark brand register.
 * `data-theme="dark"` keys the warm-graphite tokens in globals.css, so the
 * public site looks the same whatever the visitor chose in the workspace
 * (which keeps its own light/dark toggle).
 */
export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="marketing-shell flex min-h-screen flex-col" data-theme="dark">
      <MarketingNav />
      <main className="flex-1 pt-[88px] sm:pt-[100px]">{children}</main>
      <MarketingFooter />
    </div>
  );
}
