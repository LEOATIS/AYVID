import Link from "next/link";

export const metadata = {
  title: "Approach",
  description:
    "The methodology behind AYVID: deterministic accounting from SEC filings, multi-hop supply-chain propagation, and the historical shock validation.",
};

export default function ApproachPage() {
  return (
    <>
      <PageHeader
        eyebrow="Methodology"
        title="How AYVID turns a shock into a cited answer."
        lede="Three parts: calculate every number from the filing, trace who else is exposed, and test the graph against history. Each part is built so a reader can check it."
      />

      <SectionTwoCol
        eyebrow="Part 1 · Accounting from the filing"
        title="Code calculates. The model only words."
        body={[
          "Income statement, balance sheet and cash flow come live from SEC EDGAR's standardised accounting data (XBRL), each value tied to its filing and accession number. Margins, growth, free cash flow, volatility and drawdown are calculated in Python; period, currency and scale mismatches are refused rather than blended.",
          "Every sentence in an answer is labelled as a reported fact (RPT), a calculation (CALC), a management statement (MGMT), an interpretation (INTERP) or a hypothesis (HYP). A claim that cannot be cited is removed before you see it.",
        ]}
        rightTitle="Where Gemini fits"
        rightBody="Gemini 4 writes the summary in a fixed JSON format from facts the code has already verified: Hydrogen for most answers, Argon for multi-hop shocks, Carbon for reading long 10-K filings. Any number it adds is rejected."
      />

      <SectionTwoCol
        eyebrow="Part 2 · Multi-hop propagation"
        title="From the headline to the second and third order."
        body={[
          "Supply shock: a TSMC advanced-packaging squeeze reaches NVIDIA's gross margin, then Broadcom's custom chips and the hyperscalers that buy them.",
          "Incident: a Microsoft Azure and Microsoft 365 outage pushes customers to hedge with Google Cloud and AWS, and raises demand for independent observability (Datadog, Cloudflare) and security (CrowdStrike, Palo Alto Networks).",
          "Each link is ranked by how much one company depends on the other, says whether it was extracted from a filing or is an estimate, and states when the effect should show (1–5 days, then 30–90 days) and what would prove it wrong.",
        ]}
        rightTitle="Built for doubt"
        rightBody="Estimated links are labelled as estimates. Missing data reads unknown, never zero. A saved thesis re-checks the conditions that would break it on every new filing and price."
      />

      <SectionTwoCol
        eyebrow="Part 3 · Historical validation"
        title="Eight years, 20 shocks, honest costs."
        body={[
          "The Shock Lab replays 20 named shocks from 2018 to 2025 with modelled IBKR commissions and slippage. Trading only the companies the graph surfaced, inside each shock window, returned +33.43% compounded over 1,392 trades (63.7% win rate; best window +23.81% in the 2023 SVB crisis).",
          "The same engine trading every ticker daily on technicals, before risk gates, lost 99.98% over 16,168 trades, mostly to costs. With risk gates on, it refuses to trade at all. The windows were chosen in hindsight, so this validates the graph and the cost model, not alpha. A blind out-of-sample test is next on the roadmap.",
        ]}
        rightTitle="Fault isolation"
        rightBody="The paper-trading engine's risk watchdog runs as a separate C++ process that can stop the book without consulting Python. The research workspace has no import path to execution at all."
      />

      <section className="bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
          <div>
            <p className="eyebrow">See it run</p>
            <h3 className="font-display display-3 mt-3 text-fg">Ask a question, or replay a shock.</h3>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/research" className="btn-primary">Open the workspace →</Link>
            <Link href="/console/live-shock?demo=1" className="btn-ghost">Inspect the Shock Lab →</Link>
          </div>
        </div>
      </section>
    </>
  );
}

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
