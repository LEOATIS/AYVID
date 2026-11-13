import Link from "next/link";

export const metadata = {
  title: "About",
  description: "Why AYVID exists, who builds it, and exactly what the product does and does not do.",
};

const FOUNDER_FACTS = [
  { k: "Builds AYVID", v: "SEC filing ingestion, the calculation engine, the supply-chain graph, the FastAPI backend, the Next.js workspace and the backtester." },
  { k: "Mercedes-Benz R&D India", v: "Software Engineer Apprentice, 2026–." },
  { k: "EXO Drones", v: "C++ / ROS 2 middleware for GPS-denied navigation under 5 ms, a hardware-in-the-loop test suite that caught 40+ regressions, TensorRT INT8 YOLOv8 at 30+ FPS on edge hardware." },
  { k: "IEEE DataPort", v: "PySpark pipelines over 2+ PB of research data at 99.9% uptime." },
  { k: "Education", v: "B.E. Computer Science, BMS College of Engineering, Bengaluru (9.38 / 10)." },
];

export default function AboutPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">About</p>
          <h1 className="font-display display-1 mt-6 max-w-[20ch] text-fg">
            Financial research that shows its work.
          </h1>
          <p className="lede mt-7">
            AYVID is an evidence-first research workspace. Ask what a shock means for a company, and it answers with
            numbers calculated from SEC filings, every sentence linked to its source, and a map of who else is exposed
            one, two and three steps down the supply chain.
          </p>
        </div>
      </section>

      <section className="border-b border-app">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">Why we built it</p>
            <h2 className="font-display display-2 mt-3 text-fg">AI writes well. It should not do the arithmetic.</h2>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <div className="prose-body max-w-prose">
              <p>
                General AI assistants are fluent and wrong in the ways that matter in finance: mixed periods, GAAP blended
                with non-GAAP, numbers with no link back to a filing. Analysts end up re-checking everything.
              </p>
              <p>
                AYVID separates language from arithmetic. Code calculates every ratio from the standardised accounting
                data companies file with the SEC and from live daily prices. Gemini only words facts that are already
                verified, and an automatic check rejects any number it did not get from the code.
              </p>
              <p>
                The second idea is where the edge is. A headline is priced in minutes; the companies two or three steps
                away, the suppliers, customers and substitutes, reprice over weeks or quarters. AYVID walks that graph
                explicitly and says whether each link came from a filing or is an estimate.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">Founder</p>
            <h2 className="font-display display-2 mt-3 text-fg">Likhith Manjunatha</h2>
            <p className="mt-3 text-[14px] text-muted">Founder &amp; CTO · Bengaluru</p>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <p className="prose-body max-w-prose">
              I build systems where a wrong number has a real cost. Autonomous drones taught me to isolate faults and
              never trust a single component under stress, which is why AYVID keeps the AI away from the numbers and
              runs its risk watchdog as a separate C++ process.
            </p>
            <dl className="mt-8 divide-y divide-[hsl(var(--border))] border-y border-app">
              {FOUNDER_FACTS.map((f) => (
                <div key={f.k} className="grid gap-1 py-4 sm:grid-cols-[180px_1fr] sm:gap-6">
                  <dt className="text-[14px] font-medium text-fg">{f.k}</dt>
                  <dd className="text-[14px] leading-relaxed text-muted">{f.v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-6 text-[14px] text-muted">
              Hiring: a quantitative / ML research co-founder to own validation and filing extraction.{" "}
              <a href="mailto:people@ayvid.ai" className="text-fg underline">people@ayvid.ai</a>
            </p>
          </div>
        </div>
      </section>

      <section id="legal" className="border-b border-app">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">What AYVID is, and is not</p>
            <h2 className="font-display display-2 mt-3 text-fg">Research only. No orders.</h2>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <div className="prose-body max-w-prose">
              <p>
                The product is the research workspace: cited answers, company pages, monitored theses, a screener and
                watchlists. It never places trades, predicts returns or tells anyone to buy or sell, and its code has no
                path to the execution engine.
              </p>
              <p>
                The Shock Lab replays 20 historical shocks through the supply-chain graph to test whether it finds the
                right companies. It runs paper only (PAPER_TRADING=true, AUTO_EXECUTE=false), and we publish the
                result with its limits: the windows were chosen in hindsight, so it validates the graph, not alpha.
              </p>
              <p>
                AYVID is not a registered investment adviser, and nothing on this site is investment advice.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-ink">
        <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
          <h3 className="font-display display-3 text-[hsl(var(--bg))]">Want to try it?</h3>
          <Link
            href="/contact"
            className="inline-flex items-center gap-2 border border-[hsl(var(--bg)/0.3)] px-5 py-3 text-[13px] font-medium text-[hsl(var(--bg))] transition-colors hover:bg-[hsl(var(--bg))] hover:text-[hsl(var(--fg))]"
          >
            Get in touch →
          </Link>
        </div>
      </section>
    </>
  );
}
