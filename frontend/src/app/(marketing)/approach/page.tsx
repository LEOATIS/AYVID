import Link from "next/link";

export const metadata = {
  title: "Approach",
  description:
    "How AYVID trades: two-track strategy, three-tier architecture, hard risk discipline, and full cost transparency.",
};

export default function ApproachPage() {
  return (
    <>
      <PageHeader
        eyebrow="Approach"
        title="Two tracks, three tiers, one risk frame."
        lede="AYVID runs a two-track strategy on a three-tier architecture. The system was designed under the constraint that any subsystem can fail and the rest must degrade gracefully — no single agent, model, or data feed gets to take the firm down."
      />

      <SectionTwoCol
        eyebrow="Track A · Index futures & options"
        title="Direction-of-shock trades on global indices."
        body={[
          "We trade futures and short-dated options across roughly twenty index venues — S&P 500, Nasdaq 100, FTSE 100, DAX, Nikkei 225, Hang Seng, KOSPI 200, Nifty 50, Sensex, ASX 200, and others — depending on which markets are open when a shock prints.",
          "Sizing is conviction-weighted with hard −1R stops and per-index cooldowns. We never compound losses inside the same shock window.",
        ]}
        rightTitle="Why F&O first"
        rightBody="Index F&O gives the cleanest expression of macro shocks: deep liquidity, defined risk, and minimal single-name idiosyncrasy. It also lets us hedge equity exposure cheaply when an idea has narrow conviction."
      />

      <SectionTwoCol
        eyebrow="Track B · Equities"
        title="Causally-implied beneficiaries and victims."
        body={[
          "When a shock breaks, the graph proposes the names that should move — direct exposure, secondary supplier dependencies, geographic chokepoints. We screen these against liquidity and cost-to-trade in real time, and only the survivors enter the equity book.",
          "The universe is ~50+ tickers, dynamically rebuilt every cycle. There is no fixed watchlist; the engine selects what to watch from the graph itself.",
        ]}
        rightTitle="Hard rules"
        rightBody="No earnings-day positions held into the print. No overnight gap risk on conviction below threshold. No single name above 4% of risk capital. All overrideable by the supervisor agent — and every override is logged."
      />

      <SectionTwoCol
        eyebrow="Risk frame"
        title="The kill switch is independent."
        body={[
          "An out-of-process C++ watchdog monitors equity, drawdown, and order flow every tick. If equity breaches the daily loss budget, or if order flow looks anomalous (rate, sign, or imbalance), the watchdog flattens the book without consulting Python — sub-millisecond.",
          "This is the single most important architectural choice we made. It says explicitly that we don't trust our own LLMs to behave under stress.",
        ]}
        rightTitle="Cost transparency"
        rightBody="Every backtest in the operations console is reported post-cost using the IBKR Tiered commission schedule and exchange-specific slippage assumptions. We do not publish gross figures."
      />

      <FootCta />
    </>
  );
}

/* ─── shared marketing primitives (kept local to keep imports light) ── */

function PageHeader({ eyebrow, title, lede }: { eyebrow: string; title: string; lede: string }) {
  return (
    <section className="relative overflow-hidden border-b border-app">
      <div className="pointer-events-none absolute inset-0 marketing-grid opacity-[0.3]" aria-hidden />
      <div className="container-page section-y reveal">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="font-display display-1 mt-6 max-w-[20ch] text-fg">{title}</h1>
        <p className="lede mt-7">{lede}</p>
      </div>
    </section>
  );
}

function SectionTwoCol({
  eyebrow,
  title,
  body,
  rightTitle,
  rightBody,
}: {
  eyebrow: string;
  title: string;
  body: string[];
  rightTitle: string;
  rightBody: string;
}) {
  return (
    <section className="border-b border-app">
      <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
        <div className="md:col-span-7">
          <p className="eyebrow">{eyebrow}</p>
          <h2 className="font-display display-2 mt-3 text-fg">{title}</h2>
          <div className="prose-body mt-7 max-w-prose">
            {body.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
        </div>
        <aside className="md:col-span-4 md:col-start-9 self-start surface-soft p-7">
          <p className="eyebrow text-[10px]">{rightTitle}</p>
          <p className="mt-3 text-[13.5px] leading-relaxed text-fg">{rightBody}</p>
        </aside>
      </div>
    </section>
  );
}

function FootCta() {
  return (
    <section className="bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
        <div>
          <p className="eyebrow">Want to dig deeper?</p>
          <h3 className="font-display display-3 mt-3 text-fg">
            Selected research notes are public.
          </h3>
        </div>
        <Link href="/research" className="btn-primary">
          Read research →
        </Link>
      </div>
    </section>
  );
}
