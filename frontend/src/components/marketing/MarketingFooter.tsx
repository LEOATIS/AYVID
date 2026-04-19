import Link from "next/link";
import { Wordmark } from "./Wordmark";

const FOOTER_LINKS: { heading: string; items: { href: string; label: string; external?: boolean }[] }[] = [
  {
    heading: "Firm",
    items: [
      { href: "/about", label: "About" },
      { href: "/approach", label: "Approach" },
      { href: "/research", label: "Research" },
      { href: "/contact", label: "Contact" },
    ],
  },
  {
    heading: "Disclosures",
    items: [
      { href: "/about#legal", label: "Regulatory standing" },
      { href: "/about#legal", label: "Risk disclosure" },
      { href: "/about#legal", label: "Privacy policy" },
    ],
  },
  {
    heading: "For partners",
    items: [
      { href: "/contact", label: "LP enquiries" },
      { href: "/contact", label: "Press enquiries" },
      { href: "/console", label: "Operations console" },
    ],
  },
];

export function MarketingFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="border-t border-app bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y-sm">
        <div className="grid grid-cols-1 gap-10 md:grid-cols-12">
          <div className="md:col-span-4">
            <Wordmark variant="stacked" href="/" />
            <p className="mt-5 max-w-xs text-[13px] leading-relaxed text-muted">
              An AI-native hedge fund. Causal supply-chain inference, agent-driven
              research, and end-to-end execution.
            </p>
          </div>

          {FOOTER_LINKS.map((section) => (
            <div key={section.heading} className="md:col-span-2">
              <div className="eyebrow mb-3 text-[10px]">{section.heading}</div>
              <ul className="space-y-2">
                {section.items.map((item) => (
                  <li key={item.label}>
                    <Link
                      href={item.href}
                      className="text-[13px] text-fg/80 hover:text-fg"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          <div className="md:col-span-2">
            <div className="eyebrow mb-3 text-[10px]">Offices</div>
            <ul className="space-y-2 text-[13px] text-fg/80">
              <li>New York</li>
              <li>Bengaluru</li>
            </ul>
          </div>
        </div>

        <div className="mt-12 flex flex-col items-start justify-between gap-3 border-t border-app pt-6 sm:flex-row sm:items-center">
          <p className="max-w-3xl text-[11px] leading-relaxed text-subtle">
            © {year} AYVID. The materials on this site are for informational purposes only and do
            not constitute an offer to sell, or a solicitation of an offer to buy, any security or
            interest in any AYVID-managed fund. Past simulated or live performance is not indicative
            of future results. All trading involves substantial risk of loss.
          </p>
          <p className="text-[11px] uppercase tracking-[0.16em] text-subtle">
            YC S26 · AI-Native Hedge Funds
          </p>
        </div>
      </div>
    </footer>
  );
}
