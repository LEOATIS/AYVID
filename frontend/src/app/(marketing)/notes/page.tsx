import Link from "next/link";
import { CORE_QUERY, MARGIN_QUERY, OUTAGE_QUERY, researchHref } from "@/lib/queries";

export const metadata = {
  title: "Research notes",
  description:
    "Notes from AYVID on supply-shock propagation, cited financial research and historical shock validation — each one runnable in the workspace.",
};

const NOTES: { date: string; tag: string; title: string; teaser: string; href: string; cta: string }[] = [
  {
    date: "2026 · Nov",
    tag: "Live case",
    title: "A Microsoft cloud outage, two steps out.",
    teaser:
      "When Azure and Microsoft 365 go down, the first-order story is Microsoft. The second-order story is who absorbs the shock: Google Cloud and AWS as substitutes, Datadog and Cloudflare for independent resilience, CrowdStrike and Palo Alto Networks as CISOs rethink bundled security. Each link carries its timing and what would prove it wrong.",
    href: researchHref(OUTAGE_QUERY),
    cta: "Run live in workspace",
  },
  {
    date: "2026 · Nov",
    tag: "Validation",
    title: "Why a naive backtest loses 99.98% and a graph-filtered one does not.",
    teaser:
      "Trading every ticker every day on technicals lost 99.98% over eight years, mostly to costs. The same engine, trading only companies the graph surfaced inside 20 named shock windows, returned +33.43% compounded with a 63.7% win rate. The windows were chosen in hindsight: this validates the graph, not alpha.",
    href: "/console/live-shock?demo=1",
    cta: "Inspect the Shock Lab",
  },
  {
    date: "2026 · Oct",
    tag: "Graph",
    title: "What a multi-hop supply-chain graph actually buys you.",
    teaser:
      "A TSMC packaging squeeze is priced into TSMC in minutes. NVIDIA's gross margin, Broadcom's custom chips and the hyperscalers that buy them reprice over weeks. The graph makes those second- and third-order links explicit, and labels each one as filing-extracted or estimated.",
    href: researchHref(CORE_QUERY),
    cta: "Run live in workspace",
  },
  {
    date: "2026 · Oct",
    tag: "Method",
    title: "Models never write the numbers.",
    teaser:
      "Every figure in an AYVID answer is calculated in code from SEC filings and live prices. Gemini only words verified facts in a fixed JSON format, and an automatic check rejects any sentence that cites an unknown source or adds a number the code did not produce.",
    href: researchHref(MARGIN_QUERY),
    cta: "Run live in workspace",
  },
];

export default function NotesPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">Research notes</p>
          <h1 className="font-display display-1 mt-6 max-w-[18ch] text-fg">Notes you can run.</h1>
          <p className="lede mt-7">
            Each note links to the live investigation behind it, so you can check the sources and numbers yourself.
          </p>
        </div>
      </section>

      <section>
        <div className="container-page section-y">
          <ul className="space-y-px overflow-hidden border border-app bg-[hsl(var(--border))]">
            {NOTES.map((n) => (
              <li key={n.title} className="bg-panel">
                <Link href={n.href} className="group grid grid-cols-1 gap-6 p-7 transition-colors hover:bg-[hsl(var(--bg-sunken))] md:grid-cols-12">
                  <div className="md:col-span-3">
                    <div className="num text-[12px] uppercase tracking-[0.16em] text-subtle">{n.date}</div>
                    <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-brand">{n.tag}</div>
                  </div>
                  <div className="md:col-span-9">
                    <h2 className="font-display text-[24px] leading-snug text-fg transition-colors group-hover:text-brand">{n.title}</h2>
                    <p className="prose-body mt-3 max-w-prose">{n.teaser}</p>
                    <span className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-brand">{n.cta} →</span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="border-t border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
          <p className="prose-body max-w-2xl">
            The workspace and Shock Lab are open to internal users. Want access? Write to us and we&rsquo;ll reply the same day.
          </p>
          <Link href="/contact" className="btn-primary">Request access →</Link>
        </div>
      </section>
    </>
  );
}
