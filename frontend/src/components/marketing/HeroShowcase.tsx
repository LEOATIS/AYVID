import type { ReactNode } from "react";
import { Sparkles, Activity } from "lucide-react";

/**
 * The big "preview card" that sits directly below the hero headline + CTAs,
 * mirroring Polo's "Building brands to drive Results" project teaser with
 * its floating "Edit template (Free)" + "Made in Framer" ribbons.
 *
 * For AYVID we frame the live operations console inside it: top ribbon
 * advertises the live engine, bottom ribbon shows the strapline.
 */
export function HeroShowcase({ children }: { children: ReactNode }) {
  return (
    <div className="hero-showcase">
      <div className="badge-row">
        <span className="ribbon">
          <span className="grid h-3.5 w-3.5 place-items-center rounded-full bg-[hsl(var(--pos))] text-[hsl(var(--brand-fg))]">
            <Activity className="h-2 w-2" strokeWidth={3} />
          </span>
          Live engine · IB Paper
        </span>
        <span className="ribbon">
          <Sparkles className="h-3 w-3" strokeWidth={2.6} />
          Causal graph
        </span>
      </div>

      <div className="px-4 pt-12 pb-4 sm:px-6 sm:pt-14 sm:pb-6 lg:px-10 lg:pt-16 lg:pb-10">
        <div className="grid gap-6 lg:grid-cols-12 lg:items-center lg:gap-10">
          <div className="lg:col-span-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-subtle">
              Operations console
            </p>
            <h3 className="mt-3 text-[clamp(1.6rem,2.8vw,2.4rem)] font-semibold leading-[1.1] tracking-tight text-fg">
              Trading the second-order
              <br />
              consequences,{" "}
              <span className="text-[hsl(var(--fg-subtle))]">live.</span>
            </h3>
            <p className="prose-body mt-4 max-w-md text-[14.5px] leading-[1.7]">
              Each panel below is wired to the same engine that places the
              trades. Walk-forward 2018 → 2025 across 20 named historical
              shocks. No hindsight. No staging data.
            </p>
            <ul className="mt-6 grid grid-cols-2 gap-2 text-[12.5px] text-muted">
              <li className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--brand))]" />
                LangGraph swarm
              </li>
              <li className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--brand))]" />
                Neo4j live graph
              </li>
              <li className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--brand))]" />
                IB execution
              </li>
              <li className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--brand))]" />
                C++ kill-switch
              </li>
            </ul>
          </div>
          <div className="relative lg:col-span-7">
            <div className="relative mx-auto aspect-[5/4] w-full max-w-[560px] lg:max-w-none">
              {children}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
