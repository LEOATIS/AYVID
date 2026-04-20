import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  Brain,
  Cpu,
  Network,
  ShieldCheck,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";
import { Hero } from "@/components/marketing/Hero";
import { CapabilityMarquee } from "@/components/marketing/CapabilityMarquee";
import { FaqAccordion } from "@/components/marketing/FaqAccordion";

export const metadata = {
  title: "AYVID — Asymmetric Yield Via Intelligent Decisions",
  description:
    "AYVID is an AI-native hedge fund. Swarms of LLM agents read 10-Ks, earnings calls, and SEC filings, trace the consequences through a live causal graph of the global economy, and route sized, risk-limited orders into the market — end-to-end, no humans on the trade path.",
};

export default function HomePage() {
  return (
    <>
      <Hero />
      <Marquee />
      <Identity />
      <Process />
      <Capabilities />
      <Comparison />
      <Numbers />
      <Voices />
      <Faq />
      <Engage />
    </>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Capability marquee — continuous horizontal scroll of pill tags.
   ───────────────────────────────────────────────────────────────── */
function Marquee() {
  return (
    <section className="border-y border-app bg-[hsl(var(--bg-elev))] py-8">
      <CapabilityMarquee />
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Identity — "Available pill" + intro card + capability tags +
   timeline of milestones.  Mirrors Polo's "Hello I am Johan" block.
   ───────────────────────────────────────────────────────────────── */
function Identity() {
  const tags = [
    "Causal AI",
    "Agent swarms",
    "LangGraph",
    "Neo4j",
    "FastAPI",
    "Interactive Brokers",
    "Walk-forward backtests",
    "Risk-limited execution",
  ];
  const timeline = [
    { role: "AYVID founded", org: "Engine v0 · modular monolith", year: "Apr 2026" },
    { role: "Causal graph + agent swarm", org: "Neo4j + LangGraph integration", year: "Apr 2026" },
    { role: "IB Paper · live shock catalog", org: "20 named shocks · walk-forward", year: "Apr 2026" },
    { role: "YC S26 · seed conversations", org: "Allocator outreach", year: "In progress" },
  ];
  return (
    <section className="border-b border-app">
      <div className="container-page section-y">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-12 lg:gap-14">
          {/* Left: identity card */}
          <div className="lg:col-span-5">
            <span className="pill pill-status">Open to allocator capital</span>
            <h2 className="font-display display-2 mt-6 text-fg">
              Hello — we are <em className="not-italic text-brand">AYVID</em>.
            </h2>
            <p className="prose-body mt-5 max-w-md">
              An AI-native hedge fund built end-to-end around causal reasoning.
              We don&rsquo;t bolt LLMs onto a quant stack. We use them to find
              strategies that didn&rsquo;t exist before.
            </p>
            <Link href="/about" className="btn-ghost !rounded-full mt-7 inline-flex">
              About the firm <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {/* Right: capability tags + timeline */}
          <div className="lg:col-span-7">
            <p className="section-label">Capabilities</p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              {tags.map((t) => (
                <span key={t} className="pill pill-ghost !text-fg">
                  {t}
                </span>
              ))}
            </div>

            <p className="section-label mt-12">Milestones</p>
            <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {timeline.map((row) => (
                <li key={row.role} className="tile-soft !rounded-2xl !p-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="font-display text-[16px] leading-snug text-fg">
                      {row.role}
                    </p>
                    <span className="num text-[12px] text-subtle">{row.year}</span>
                  </div>
                  <p className="mt-1 text-[12.5px] text-muted">{row.org}</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Process — 4 numbered cards.  Polo's "How it works" → AYVID's
   "How alpha is generated".
   ───────────────────────────────────────────────────────────────── */
function Process() {
  const steps = [
    {
      n: "01",
      title: "Ingest the world",
      body: "Live SEC filings, earnings calls, central-bank prints, OPEC headlines, social-media noise. NER + embedding indexes everything in seconds.",
    },
    {
      n: "02",
      title: "Map the consequence",
      body: "A live Neo4j graph of suppliers, customers, indices, and shared inputs propagates each shock N hops outward to find the unobvious beneficiaries and victims.",
    },
    {
      n: "03",
      title: "Decide with a swarm",
      body: "Four LLM specialists — Strategist, Mapper, Auditor, Supervisor — debate every signal. Bayesian reconciliation across them is how we estimate conviction.",
    },
    {
      n: "04",
      title: "Execute end-to-end",
      body: "Sized, hedged, risk-limited orders route to Interactive Brokers in under 100 ms. A C++ kill-switch watchdog can flatten the book in milliseconds.",
    },
  ];
  return (
    <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="section-label">How it works</p>
            <h2 className="font-display display-2 mt-4 max-w-2xl text-fg">
              Process is <em className="not-italic text-brand">everything</em>.
            </h2>
            <p className="prose-body mt-5 max-w-xl">
              A simple, four-stage pipeline that turns unstructured language
              into structured, sized, risk-limited orders.
            </p>
          </div>
          <Link href="/approach" className="pill pill-ghost !text-fg">
            See the architecture <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-4 md:grid-cols-2">
          {steps.map((s) => (
            <article key={s.n} className="process-card">
              <div className="flex items-center gap-4">
                <span className="process-num num">{s.n}</span>
                <span className="text-[11px] uppercase tracking-[0.16em] text-subtle">
                  Step {s.n}
                </span>
              </div>
              <h3 className="font-display mt-5 text-[22px] leading-tight text-fg">
                {s.title}
              </h3>
              <p className="mt-3 text-[14px] leading-relaxed text-muted">{s.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Capabilities — 4-up service-style cards with icons.
   ───────────────────────────────────────────────────────────────── */
function Capabilities() {
  const items = [
    {
      icon: <Network className="h-5 w-5" />,
      title: "Causal supply-chain graph",
      body: "Live Neo4j topology of suppliers, customers, indices, and shared inputs. Shock propagation in milliseconds.",
    },
    {
      icon: <Brain className="h-5 w-5" />,
      title: "Agent-swarm research",
      body: "Four LLM specialists debate each signal. Disagreement is itself a signal that the trade is fragile.",
    },
    {
      icon: <Workflow className="h-5 w-5" />,
      title: "End-to-end execution",
      body: "From 8-K to broker order in one modular monolith. No humans on the trade path. Tier 1 sub-100ms.",
    },
    {
      icon: <ShieldCheck className="h-5 w-5" />,
      title: "Risk-first by design",
      body: "Independent C++ kill-switch watchdog. Position-level VaR, drawdown, and concentration limits enforced pre-trade.",
    },
  ];
  return (
    <section className="border-b border-app">
      <div className="container-page section-y">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="section-label">What we run</p>
            <h2 className="font-display display-2 mt-4 max-w-2xl text-fg">
              Capabilities, <em className="not-italic text-brand">not promises</em>.
            </h2>
          </div>
          <Link href="/console" className="pill pill-brand">
            See it live <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        <ul className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((it) => (
            <li key={it.title} className="tile">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[hsl(var(--bg-sunken))] text-fg">
                {it.icon}
              </div>
              <h3 className="font-display mt-6 text-[18px] leading-tight text-fg">
                {it.title}
              </h3>
              <p className="mt-2 text-[13.5px] leading-relaxed text-muted">{it.body}</p>
            </li>
          ))}
        </ul>

        {/* Sub-list of supporting features (Polo's "small services" row) */}
        <div className="mt-8 flex flex-wrap gap-2">
          {[
            "Walk-forward backtests",
            "Live shock catalog · 20 events",
            "Multi-broker adapter",
            "Real-time PnL attribution",
            "Operations console",
            "Research notes",
          ].map((t) => (
            <span key={t} className="pill pill-ghost">
              {t}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   AYVID vs Traditional — Polo's "Why me" comparison block.
   ───────────────────────────────────────────────────────────────── */
function Comparison() {
  const rows = [
    {
      us:   { title: "Causal reasoning",         body: "We trace each shock through suppliers, customers, and indices. We trade the unobvious second-order beneficiaries." },
      them: { title: "Factor regression",        body: "Lagged factors, momentum residuals, value premia. Linear models on non-linear consequences." },
    },
    {
      us:   { title: "Agent swarms",             body: "Four specialists debate every signal. Disagreement is conviction-discounted." },
      them: { title: "Single research analyst",  body: "One PM's mental model bottlenecks the entire firm. Disagreement is dampened, not measured." },
    },
    {
      us:   { title: "End-to-end automation",    body: "8-K to broker order in one system. No humans on the trade path. Risk pre-checked." },
      them: { title: "Human in the loop",        body: "Analysts hand-off to traders. Latency in seconds-to-minutes. Behavioural drift in execution." },
    },
    {
      us:   { title: "Verifiable in operations",  body: "Live shock catalog, walk-forward equity curves, runner status — all reproducible from the console." },
      them: { title: "Quarterly tear sheets",     body: "Aggregated, smoothed, lagged. Hard to attribute alpha to actual decisions." },
    },
  ];
  return (
    <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y">
        <div className="max-w-2xl">
          <p className="section-label">Why AYVID</p>
          <h2 className="font-display display-2 mt-4 text-fg">
            What separates us from a traditional fund.
          </h2>
          <p className="prose-body mt-5">
            Most quants will bolt LLMs onto an existing book. We started from
            language and worked outward to execution. The architecture is the
            edge.
          </p>
        </div>

        <div className="mt-12 grid gap-3">
          {rows.map((r, i) => (
            <div key={i} className="compare-row">
              <div className="compare-cell is-us">
                <span className="compare-tag is-us">AYVID</span>
                <h3 className="font-display mt-4 text-[19px] text-fg">{r.us.title}</h3>
                <p className="mt-2 text-[13.5px] leading-relaxed text-muted">{r.us.body}</p>
              </div>
              <div className="compare-cell is-them">
                <span className="compare-tag is-them">Traditional fund</span>
                <h3 className="font-display mt-4 text-[19px] text-fg">{r.them.title}</h3>
                <p className="mt-2 text-[13.5px] leading-relaxed text-muted">{r.them.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Numbers — Polo's "$250m revenue / 100+ clients" stat block.
   ───────────────────────────────────────────────────────────────── */
function Numbers() {
  const stats = [
    { v: "20",   l: "Named historical shocks",  s: "studied 2018 → 2025" },
    { v: "4",    l: "Specialist agents",        s: "per signal decision" },
    { v: "<100", l: "ms tier-1 execution",      s: "to interactive brokers" },
    { v: "8 yr", l: "Out-of-sample span",       s: "walk-forward, no peeking" },
  ];
  return (
    <section className="border-b border-app">
      <div className="container-page section-y">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="section-label">By the numbers</p>
            <h2 className="font-display display-2 mt-4 max-w-2xl text-fg">
              All figures are verifiable in the console.
            </h2>
          </div>
          <Link href="/console" className="pill pill-brand">
            Verify live <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        <ul className="mt-12 grid grid-cols-2 gap-px overflow-hidden rounded-3xl border border-app bg-[hsl(var(--border))] md:grid-cols-4">
          {stats.map((s) => (
            <li key={s.l} className="bg-panel p-7 sm:p-8">
              <div className="stat-block">
                <div className="stat-number">{s.v}</div>
                <div className="mt-2 text-[13px] font-medium text-fg">{s.l}</div>
                <div className="text-[11px] uppercase tracking-[0.14em] text-subtle">{s.s}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Voices — testimonial-style cards (Polo's testimonials).  We use
   research / RFS quotes since AYVID is pre-LP.
   ───────────────────────────────────────────────────────────────── */
function Voices() {
  const quotes = [
    {
      q: "The next Renaissance, Bridgewater, and D.E.\u00a0Shaw will be built on AI. The biggest funds will be slow to adapt.",
      who: "Charlie Holtz",
      where: "Y Combinator · Request for Startups",
      tag: "RFS · AI-native hedge funds",
    },
    {
      q: "The alpha is in using AI to come up with strategies that didn\u2019t exist before — language comprehension, causal reasoning across thousands of filings.",
      who: "Charlie Holtz",
      where: "Y Combinator · Request for Startups",
      tag: "RFS · Strategy generation",
    },
    {
      q: "Causal inference on supply chains is the most underweighted edge in markets. The data has been there for years; the reasoning hasn\u2019t.",
      who: "Working note",
      where: "AYVID Research · 2026",
      tag: "Internal · Causal edge",
    },
  ];
  return (
    <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="section-label">Voices</p>
            <h2 className="font-display display-2 mt-4 max-w-2xl text-fg">
              The thesis, in other people&rsquo;s words.
            </h2>
          </div>
        </div>

        <ul className="mt-12 grid grid-cols-1 gap-5 md:grid-cols-3">
          {quotes.map((qq) => (
            <li key={qq.q} className="tile">
              <span className="pill pill-ghost text-[10.5px] uppercase tracking-[0.16em]">
                {qq.tag}
              </span>
              <p className="font-display mt-5 text-[18px] leading-snug text-fg">
                &ldquo;{qq.q}&rdquo;
              </p>
              <div className="mt-6 flex items-center gap-3">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-[hsl(var(--bg-sunken))] font-display text-[14px] text-fg">
                  {qq.who.split(" ").map((w) => w[0]).join("").slice(0, 2)}
                </span>
                <div>
                  <p className="text-[13px] font-medium text-fg">{qq.who}</p>
                  <p className="text-[11.5px] text-muted">{qq.where}</p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   FAQ — Polo's accordion FAQ, adapted for allocator-style questions.
   ───────────────────────────────────────────────────────────────── */
function Faq() {
  const items = [
    {
      q: "What does AYVID actually trade?",
      a: "Two tracks. (1) Equities across a dynamically screened universe of ~50 names — long/short via shock-driven, causal beneficiaries. (2) Index F&O across ~20 global indices for hedged macro exposure. The operations console shows live positions and signal sources.",
    },
    {
      q: "Where does the alpha come from?",
      a: "Causal reasoning over a live supply-chain graph. When a shock arrives, agents propagate it N hops to find unobvious beneficiaries and victims, then size the trade against historical co-movement. The edge is in the second-order chain, not the first headline.",
    },
    {
      q: "Is the LLM in the critical execution path?",
      a: "No. Tier 1 (deterministic, < 100 ms) routes orders. Tier 2 (causal graph, ~1 s) re-weights conviction. Tier 3 (LLM swarm, ~30 s) can override — emit, modify, or cancel — but is never a blocker for the fast path.",
    },
    {
      q: "How is risk managed?",
      a: "Position-level VaR, drawdown, concentration, and exposure limits are enforced pre-trade. An independent C++ kill-switch watchdog runs in its own process and can flatten the entire book in milliseconds if it detects anomalies.",
    },
    {
      q: "What stage are you at?",
      a: "Production engine, IB Paper-traded, walk-forward backtested 2018→2025 across 20 named historical shocks. We are running paper for the seed round and intend to convert to a regulated structure with first allocator capital.",
    },
    {
      q: "How can an allocator engage?",
      a: "We are selective by design. The fastest path is the contact form on /contact — please include AUM, mandate, and the typical allocation size you consider for early managers.",
    },
  ];
  return (
    <section className="border-b border-app">
      <div className="container-page section-y">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-12 lg:gap-14">
          <div className="lg:col-span-4">
            <p className="section-label">FAQ</p>
            <h2 className="font-display display-2 mt-4 text-fg">
              Questions, answered.
            </h2>
            <p className="prose-body mt-5">
              Most of what we hear from allocators on the first call falls into
              one of these. The rest is in the operations console.
            </p>
            <Link href="/contact" className="pill pill-ghost mt-6 !text-fg">
              Ask another <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="lg:col-span-8">
            <FaqAccordion items={items} />
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Engage — two big cards (allocator vs research) → Polo's pricing block.
   ───────────────────────────────────────────────────────────────── */
function Engage() {
  return (
    <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
      <div className="container-page section-y">
        <div className="max-w-2xl">
          <p className="section-label">Engage with AYVID</p>
          <h2 className="font-display display-2 mt-4 text-fg">
            Two ways in. Both start with a conversation.
          </h2>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-5 md:grid-cols-2">
          {/* Allocator — primary, ink-filled */}
          <article className="tile-ink flex flex-col">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] opacity-70">
              <Cpu className="h-3.5 w-3.5" /> Allocator track
            </div>
            <h3 className="font-display mt-6 text-[28px] leading-tight">
              Live operations review.
            </h3>
            <p className="mt-3 text-[14px] leading-relaxed opacity-80">
              For LPs, family offices, and YC partners. Walk through the live
              shock catalog, signal book, and IB Paper trades. Sign an MNDA for
              equity-curve and risk-figure access.
            </p>
            <div className="mt-auto flex flex-wrap gap-2 pt-8">
              <Link
                href="/contact"
                className="inline-flex items-center gap-2 rounded-full bg-[hsl(var(--bg))] px-5 py-3 text-[13px] font-medium text-[hsl(var(--fg))] transition-transform hover:-translate-y-0.5"
              >
                Speak with the desk <ArrowRight className="h-3.5 w-3.5" />
              </Link>
              <Link
                href="/console"
                className="inline-flex items-center gap-2 rounded-full border border-[hsl(var(--bg)/0.25)] px-5 py-3 text-[13px] font-medium opacity-90 transition-transform hover:-translate-y-0.5 hover:opacity-100"
              >
                Operations console <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </article>

          {/* Research — secondary, soft */}
          <article className="tile flex flex-col">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-subtle">
              <Zap className="h-3.5 w-3.5" /> Research track
            </div>
            <h3 className="font-display mt-6 text-[28px] leading-tight text-fg">
              Notes, methodology, and the engine roadmap.
            </h3>
            <p className="prose-body mt-3 text-[14px]">
              Public research notes on causal inference, agent-swarm
              calibration, and walk-forward methodology. No live PnL.
            </p>
            <div className="mt-auto flex flex-wrap gap-2 pt-8">
              <Link href="/research" className="btn-primary !rounded-full !px-5 !py-3 transition-transform hover:-translate-y-0.5">
                Research notes <ArrowRight className="h-3.5 w-3.5" />
              </Link>
              <Link href="/approach" className="btn-ghost !rounded-full !px-5 !py-3 transition-transform hover:-translate-y-0.5">
                Architecture
              </Link>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
