import Link from "next/link";

export const metadata = {
  title: "About",
  description:
    "Why AYVID exists, how it's built, and the regulatory standing under which it operates.",
};

export default function AboutPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">About</p>
          <h1 className="font-display display-1 mt-6 max-w-[20ch] text-fg">
            A fund built the way an AI lab would build one.
          </h1>
          <p className="lede mt-7">
            AYVID is built on the conviction that the next great quantitative
            firms will not be hedge funds with an AI team. They&rsquo;ll be AI
            systems that happen to trade. That conviction shapes every choice
            below.
          </p>
        </div>
      </section>

      <section className="border-b border-app">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">Why now</p>
            <h2 className="font-display display-2 mt-3 text-fg">
              The 1985 moment, but for language models.
            </h2>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <div className="prose-body max-w-prose">
              <p>
                In the mid-1980s, a handful of funds — Renaissance, D. E. Shaw,
                early Citadel — quietly began using computers to find patterns
                in markets. It looked silly. Their LPs were skeptical. A decade
                later, that work had become the most valuable strategy class in
                the world.
              </p>
              <p>
                We&rsquo;re at a similar inflection point with AI. The biggest
                funds in the world have been slow to adapt — partly because
                compliance won&rsquo;t let them, partly because the org chart
                doesn&rsquo;t allow it. <strong>That is the opening.</strong>
              </p>
              <p>
                AYVID exists because we believe the alpha isn&rsquo;t in bolting
                LLMs onto existing strategies. It&rsquo;s in using LLMs to find
                strategies that didn&rsquo;t exist before — strategies that
                require reading, comprehending, and reasoning across the
                informational firehose of the modern economy.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">Engineering philosophy</p>
            <h2 className="font-display display-2 mt-3 text-fg">
              Modular monolith. One repo. No microservice cosplay.
            </h2>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <div className="prose-body max-w-prose">
              <p>
                The whole stack — ingestion, NER, graph propagation, agent
                debate, sizing, routing, kill switch — lives in one Python +
                C++ repository, structured as a modular monolith. We deploy it
                as one binary on one box, and add scale by replicating the
                whole thing.
              </p>
              <p>
                That decision is unfashionable, and it is the right one for a
                team this size. It removes an entire class of latency, an
                entire class of contract drift, and an entire class of failure
                where one half of the trade path doesn&rsquo;t know what the
                other half is doing.
              </p>
              <p>
                The boundaries we do enforce are between the deterministic
                execution path (Tier 1, sub-100 ms) and the LLM reasoning path
                (Tier 3, ~30 s). The slow tier is allowed to override the fast
                one — but never to be on the critical path.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section id="legal" className="border-b border-app">
        <div className="container-page section-y grid grid-cols-1 gap-12 md:grid-cols-12">
          <div className="md:col-span-4">
            <p className="eyebrow">Regulatory standing</p>
            <h2 className="font-display display-2 mt-3 text-fg">
              We are explicit about where we are on the path.
            </h2>
          </div>
          <div className="md:col-span-7 md:col-start-6">
            <div className="prose-body max-w-prose">
              <p>
                AYVID is in pre-launch as an investment manager. We are not yet
                a registered investment adviser, and the materials on this site
                do not constitute an offer to sell, or a solicitation of an
                offer to buy, any security or interest in any AYVID-managed
                fund.
              </p>
              <p>
                Our path to launch is the standard one for a research-led
                fund: state-level RIA registration, prime-broker selection,
                administrator and auditor onboarding, and a friends-and-family
                structure followed by a 3(c)(1) fund. The system is being run
                live in paper today against Interactive Brokers; production
                capital follows the regulatory milestones, not the other way
                around.
              </p>
              <p>
                Until then, all live performance lives in the operations
                console under the disclosure that it is paper, and all
                historical performance is reported strictly post-cost.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-ink">
        <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
          <h3 className="font-display display-3 text-[hsl(var(--bg))]">
            Want to talk?
          </h3>
          <Link
            href="/contact"
            className="inline-flex items-center gap-2 border border-[hsl(var(--bg)/0.3)] px-5 py-3 text-[13px] font-medium text-[hsl(var(--bg))] transition-colors hover:bg-[hsl(var(--bg))] hover:text-ink"
          >
            Speak with the desk →
          </Link>
        </div>
      </section>
    </>
  );
}
