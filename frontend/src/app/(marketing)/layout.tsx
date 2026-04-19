import type { ReactNode } from "react";
import { MarketingNav } from "@/components/marketing/MarketingNav";
import { MarketingFooter } from "@/components/marketing/MarketingFooter";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="marketing-shell flex min-h-screen flex-col">
      <MarketingNav />
      <main className="flex-1 pt-[88px] sm:pt-[100px]">{children}</main>
      <MarketingFooter />
    </div>
  );
}
