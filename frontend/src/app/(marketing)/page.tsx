import Link from "next/link";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { ResearchLauncher } from "@/components/marketing/ResearchLauncher";
import { OUTAGE_QUERY, PACKAGING_QUERY, researchHref } from "@/lib/queries";

export const metadata = {
  title: { absolute: "AYVID — Evidence-first AI financial research" },
  description:
    "When a shock hits the market, AYVID traces who is exposed one, two and three steps away, and proves every number from the SEC filing it came from.",
};

const LIVE_CASES = [
  {
    tag: "Live case · Cloud outage",
    title: "Microsoft Azure and Microsoft 365 outage",
    path: "MSFT → GOOGL · AMZN · DDOG · CRWD · NET",
    body: "Who absorbs the shock: cloud substitutes, observability and security vendors, each link with its timing and what would prove it wrong.",
    href: researchHref(OUTAGE_QUERY),
  },
  {
    tag: "Live case · Semiconductors",
    title: "TSMC advanced-packaging bottleneck",
    path: "TSM → NVDA · AVGO · MSFT",
    body: "How a CoWoS capacity squeeze reaches NVIDIA's margins, Broadcom's custom chips and the hyperscalers that buy them.",
    href: researchHref(PACKAGING_QUERY),
  },
];

const LOOP = [
  { k: "Ask", v: "A question about a company, an event or a comparison. Tickers are resolved per exchange; ambiguity is surfaced, not guessed." },
  { k: "Retrieve", v: "Income statement, balance sheet and cash flow live from SEC EDGAR, daily prices from a live feed, and the supply-chain graph." },
  { k: "Verify", v: "Margins, growth, volatility and drawdown are computed in Python. Period, currency and scale mismatches are refused." },
  { k: "Analyse", v: "Shocks propagate through supplier, customer and competitor links, ranked by how much each company depends on the other." },
  { k: "Inspect", v: "Click any citation to see the filing, its accession number and timestamp, or the formula and inputs behind a number." },
  { k: "Monitor", v: "Save a thesis with the conditions that would break it. “What changed?” re-checks them; missing data reads unknown, never zero." },
];

const STATES = [
  { k: "Reported fact", m: "RPT" },
  { k: "Deterministic calculation", m: "CALC" },
  { k: "Management statement", m: "MGMT" },
  { k: "Model interpretation", m: "INTERP" },
  { k: "Unverified hypothesis", m: "HYP" },
];

const PROOF = [
  { v: "+33.43%", k: "Graph-filtered, compounded", tone: "text-pos" },
  { v: "−99.98%", k: "Naive, every ticker daily", tone: "text-neg" },
  { v: "1,392", k: "Trades (vs 16,168 naive)", tone: "text-fg" },
  { v: "63.7%", k: "Win rate", tone: "text-fg" },
];

const STACK = [
  { k: "Synthesis", v: "Google Gemini 4: Hydrogen writes answer summaries, Argon reasons through multi-hop shocks, Carbon reads long 10-K filings. Without a key the engine runs fully deterministically." },
  { k: "Guardrails", v: "Prompt-injection sanitiser, a boundary that refuses trade execution and return guarantees, and a citation-and-number check on every model sentence." },
  { k: "Cloud", v: "API on Google Cloud Run, execution VM on Compute Engine, images in Artifact Registry, keys in Secret Manager, keyless deploys via Workload Identity Federation." },
  { k: "Isolation", v: "The research workspace has no import path to the execution engine (enforced in CI). The Shock Lab is paper only." },
];

export default function HomePage() {
  return (
    <>
      <section className="container-page" style={{ paddingTop: "var(--s-6)", paddingBottom: "var(--s-6)" }}>
        <div className="grid gap-12 lg:grid-cols-[1.1fr_0.9fr] lg:items-end">
          <div className="min-w-0">
            <span className="eyebrow-pill">Evidence-first AI financial research</span>
            <h1 className="wordmark-polo" style={{ marginTop: "var(--s-5)" }}>
              <span className="tone-bright">Trace who&apos;s exposed.</span>
              <br />
              <span className="tone-mute">Prove every number.</span>
            </h1>
            <p className="text-fg-muted" style={{ marginTop: "var(--s-5)", maxWidth: "60ch", lineHeight: "var(--lh-body)" }}>
              When a shock hits the market, general AI tools summarise the headline and guess at the arithmetic. AYVID
              calculates every figure from SEC filings in code, links every sentence to its source, maps who is exposed
              one, two and three steps away, and keeps checking your thesis as new data lands.
            </p>
            <div className="flex flex-wrap items-center" style={{ marginTop: "var(--s-5)", gap: "var(--s-3)" }}>
              <Link href="/research" className="btn-primary !rounded-full !px-5 !py-3">
                Open the workspace <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </Link>
              <Link href="/approach" className="btn-ghost !rounded-full !px-5 !py-3">How it works</Link>
            </div>
            <p className="mt-5 text-[12px] text-subtle">
              Research only: no orders, no return predictions. The workspace is open to internal users.
            </p>
          </div>
          <ResearchLauncher />
        </div>
      </section>

      <section className="container-page" style={{ paddingBottom: "var(--s-7)" }}>
        <ul className="grid gap-4 md:grid-cols-2">
          {LIVE_CASES.map((c) => (
            <li key={c.title}>
              <Link href={c.href} className="group block h-full rounded-lg border border-app bg-[hsl(var(--bg-elev))] p-6 transition-colors hover:border-[hsl(var(--brand-dim))]">
                <p className="label-mono text-brand">{c.tag}</p>
                <h2 className="mt-3 font-display text-[22px] leading-snug text-fg">{c.title}</h2>
                <p className="num mt-2 text-[13px] text-fg">{c.path}</p>
                <p className="mt-3 text-[14px] leading-relaxed text-muted">{c.body}</p>
                <span className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-brand">
                  Run it live <ArrowUpRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="border-y border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm">
          <p className="section-label">The research loop</p>
          <h2 className="display-section mt-3 max-w-[22ch] text-fg">Ask, retrieve, verify, analyse, inspect, monitor.</h2>
          <ol className="mt-10 grid gap-px overflow-hidden border border-app bg-[hsl(var(--border))] md:grid-cols-2 lg:grid-cols-3">
            {LOOP.map((s, i) => (
              <li key={s.k} className="bg-[hsl(var(--bg-elev))] p-6">
                <p className="num text-[12px] text-subtle">0{i + 1}</p>
                <h3 className="mt-2 text-[17px] font-medium text-fg">{s.k}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-muted">{s.v}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="container-page section-y-sm">
        <div className="grid gap-10 lg:grid-cols-2">
          <div>
            <p className="section-label">Provenance you can audit</p>
            <h2 className="display-section mt-3 text-fg">Models never write the numbers.</h2>
            <p className="mt-4 max-w-[56ch] text-[15px] leading-relaxed text-muted">
              A reported figure, a computed ratio, a management statement and a model&apos;s interpretation are different
              kinds of evidence. AYVID labels each sentence with which one it is, shows stale data with its as-of date,
              and drops any claim it cannot cite instead of filling the gap.
            </p>
          </div>
          <ul className="self-end divide-y divide-[hsl(var(--border))] border-y border-app">
            {STATES.map((s) => (
              <li key={s.m} className="flex items-baseline justify-between py-3">
                <span className="text-[15px] text-fg">{s.k}</span>
                <span className="num text-[12px] text-brand">{s.m}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="border-y border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm">
          <p className="section-label">Shock Lab · 20 historical shocks, 2018–2025</p>
          <h2 className="display-section mt-3 max-w-[26ch] text-fg">The supply-chain graph, tested against eight years of shocks.</h2>
          <dl className="mt-10 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
            {PROOF.map((p) => (
              <div key={p.k} className="border-t border-app pt-4">
                <dd className={`num text-[32px] leading-none ${p.tone}`}>{p.v}</dd>
                <dt className="mt-3 text-[13px] text-muted">{p.k}</dt>
              </div>
            ))}
          </dl>
          <p className="mt-8 max-w-[72ch] text-[14px] leading-relaxed text-muted">
            Net of modelled IBKR commissions and slippage. Trading only the companies the graph surfaced, inside each shock
            window, returned +33.43% (best: +23.81% in the 2023 SVB crisis). The same engine trading every ticker daily,
            before risk gates, lost 99.98%, mostly to costs. The windows were chosen in hindsight, so this validates the
            graph and the cost model, not alpha; a blind out-of-sample test is on the roadmap.
          </p>
          <Link href="/console/live-shock?demo=1" className="btn-ghost mt-6 !rounded-full !px-5 !py-3">
            Inspect the Shock Lab <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </div>
      </section>

      <section className="container-page section-y-sm">
        <p className="section-label">Architecture</p>
        <h2 className="display-section mt-3 max-w-[24ch] text-fg">Deterministic where it matters, generative where it helps.</h2>
        <dl className="mt-10 grid gap-8 md:grid-cols-2">
          {STACK.map((s) => (
            <div key={s.k} className="border-t border-app pt-4">
              <dt className="label-mono">{s.k}</dt>
              <dd className="mt-2 text-[14px] leading-relaxed text-muted">{s.v}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="border-t border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="section-label">Start here</p>
            <h2 className="display-section mt-3 max-w-[20ch] text-fg">One question. Every source one click away.</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/research" className="btn-primary !rounded-full !px-5 !py-3">Open the workspace <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>
            <Link href="/notes" className="btn-ghost !rounded-full !px-5 !py-3">Research notes</Link>
            <Link href="/contact" className="btn-ghost !rounded-full !px-5 !py-3">Request access</Link>
          </div>
        </div>
      </section>
    </>
  );
}
