import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { ResearchLauncher } from "@/components/marketing/ResearchLauncher";

export const metadata = {
  title: { absolute: "AYVID — AI-Native Financial Research Workspace" },
  description:
    "AYVID turns market events into evidence-backed research and continuously monitors the thesis. Every claim is cited to a filing or a deterministic calculation.",
};

const LOOP = [
  { k: "Ask", v: "A question about a company, an event or a comparison. Tickers are resolved per exchange; ambiguity is surfaced, not guessed." },
  { k: "Retrieve", v: "SEC EDGAR filings when reachable, curated filing summaries, local daily price series and the supply-chain graph." },
  { k: "Verify", v: "Margins, growth, volatility and drawdown are computed in Python with Decimal. Period, currency and scale mismatches are refused." },
  { k: "Analyse", v: "Shocks propagate through documented and inferred supplier and customer edges, ranked by dependency × criticality." },
  { k: "Inspect", v: "Click any [S1] or [CALC-1] to see the excerpt, accession, timestamps, content hash or the formula and its inputs." },
  { k: "Monitor", v: "Save a thesis with invalidation conditions. “What changed?” re-evaluates them; missing data is UNKNOWN, never zero." },
];

const STATES = [
  { k: "Reported fact", m: "RPT" },
  { k: "Deterministic calculation", m: "CALC" },
  { k: "Management statement", m: "MGMT" },
  { k: "Model interpretation", m: "INTERP" },
  { k: "Unverified hypothesis", m: "HYP" },
];

const STACK = [
  { k: "Synthesis", v: "Google Gemini 4 (Hydrogen for answers, Argon for multi-hop reasoning, Carbon for filing reading), Anthropic or OpenAI — chosen by configured key. Optional: without a key the engine runs deterministically." },
  { k: "Guardrails", v: "Unicode / hidden-HTML prompt-injection sanitiser, intent boundary for trade execution and return guarantees, citation + number audit on every LLM sentence." },
  { k: "Storage", v: "SQLite in WAL mode with schema migrations — boots with zero external services; Neo4j and Qdrant enrich when running." },
  { k: "Isolation", v: "The research workspace has no import path to the execution engine (enforced by import-linter). Paper trading stays the default in the Quant Lab." },
];

export default function HomePage() {
  return (
    <>
      <section className="container-page" style={{ paddingTop: "var(--s-6)", paddingBottom: "var(--s-7)" }}>
        <div className="grid gap-12 lg:grid-cols-[1.1fr_0.9fr] lg:items-end">
          <div className="min-w-0">
            <span className="eyebrow-pill">AI-native financial research workspace</span>
            <h1 className="wordmark-polo" style={{ marginTop: "var(--s-5)" }}>
              <span className="tone-bright">Evidence first.</span>
              <br />
              <span className="tone-mute">Then the thesis.</span>
            </h1>
            <p className="text-fg-muted" style={{ marginTop: "var(--s-5)", maxWidth: "60ch", lineHeight: "var(--lh-body)" }}>
              AYVID turns market events into evidence-backed research and keeps monitoring the thesis. Every sentence carries a
              citation to a filing or a deterministic calculation — and the claims it cannot cite are dropped.
            </p>
            <div className="flex flex-wrap items-center" style={{ marginTop: "var(--s-5)", gap: "var(--s-3)" }}>
              <Link href="/research" className="btn-primary !rounded-full !px-5 !py-3">
                Open the workspace <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </Link>
              <Link href="/approach" className="btn-ghost !rounded-full !px-5 !py-3">How it works</Link>
            </div>
            <p className="mt-5 text-[12px] text-subtle">Research only. No orders, no return predictions, no buy or sell signals.</p>
          </div>
          <ResearchLauncher />
        </div>
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
            <h2 className="display-section mt-3 text-fg">Five epistemic states, labelled once per claim.</h2>
            <p className="mt-4 max-w-[56ch] text-[15px] leading-relaxed text-muted">
              A reported figure, a computed ratio, a management statement and a model&apos;s interpretation are different kinds of
              evidence. AYVID keeps them apart, flags stale data with its as-of date, and shows a degraded-mode banner when an
              upstream provider is unavailable instead of filling the gap.
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
        </div>
      </section>

      <section className="container-page section-y-sm">
        <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="section-label">Start here</p>
            <h2 className="display-section mt-3 max-w-[20ch] text-fg">One question. Every source one click away.</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/research" className="btn-primary !rounded-full !px-5 !py-3">Open the workspace <ArrowRight className="h-3.5 w-3.5" aria-hidden /></Link>
            <Link href="/console/live-shock?demo=1" className="btn-ghost !rounded-full !px-5 !py-3">Shock Lab (paper only)</Link>
            <Link href="/notes" className="btn-ghost !rounded-full !px-5 !py-3">Research notes</Link>
          </div>
        </div>
      </section>
    </>
  );
}
