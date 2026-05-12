"use client";

import Link from "next/link";
import { type CSSProperties, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";

/**
 * AYVID Hero — text on warm paper, per brief §5.2.
 *
 * The hero is no longer a stage with an ambient orb and a grid backdrop.
 * It is a printed page.
 *
 *  • 180px top padding — the breath that signals confidence.
 *  • Eyebrow:   .label-mono · color var(--brand)        · 12px above headline.
 *  • Headline:  .display-hero · TWO lines max
 *                  - line 1 in --fg
 *                  - line 2 in --brand (SOLID gold, not a gradient).
 *  • Subhead:   .display-callout (italic Fraunces) · max-width 56ch · --fg-2.
 *  • CTAs:      .btn-paper-primary + .btn-paper-secondary, 16px gap.
 *  • Below CTAs: 96px of whitespace, then a single horizontal rule.
 *  • Below the rule: four stat tiles AS A ROW OF TEXT — no cards, no
 *                    borders around the whole strip.  Vertical 1px rules
 *                    between columns.
 *
 * Heavy verbs and brand-defining facts only.  No HeroVisual, no
 * HeroShowcase, no Reveal animation, no cursor-tracking glow.  Per
 * brief §7: "Do not animate on page load except the count-up for the
 * main hero stats" — and the four stats here are static facts, not
 * live counters, so we don't animate at all.  Restraint is the design.
 */

/** Static fact set — fixed, not from any API.  Brief §5.2 verbatim. */
const STAT_TILES: {
  value: string;
  label: string;
  desc: string;
}[] = [
  {
    value: "300+",
    label: "Instruments",
    desc: "Equities · FX · Crypto · Commodities",
  },
  {
    value: "30",
    label: "Exchanges",
    desc: "Across 6 continents",
  },
  {
    value: "6",
    label: "Asset classes",
    desc: "All live-wired",
  },
  {
    value: "0",
    label: "Humans on trade path",
    desc: "End-to-end autonomous",
  },
];

export function Hero() {
  return (
    <section className="relative">
      <div
        className="container-page mx-auto"
        style={{
          paddingTop: 180,
          paddingBottom: 96,
        }}
      >
        {/* Eyebrow — .label-mono coloured gold, sits 12px above headline */}
        <p
          className="label-mono"
          style={{
            color: "hsl(var(--brand))",
            marginBottom: 12,
          }}
        >
          AYVID · AI-Native Hedge Fund
        </p>

        {/* Headline — two lines max.  Line 1 in --fg, line 2 in --brand
            (SOLID gold; never a gradient per brief §7). */}
        <h1 className="display-hero" style={{ maxWidth: "16ch" }}>
          <span style={{ color: "hsl(var(--fg))", display: "block" }}>
            Asymmetric yield
          </span>
          <span style={{ color: "hsl(var(--brand))", display: "block" }}>
            via intelligent decisions.
          </span>
        </h1>

        {/* Subhead — italic Fraunces, narrow editorial measure */}
        <p
          className="display-callout"
          style={{ marginTop: 32, maxWidth: "56ch" }}
        >
          Six asset classes. 300+ instruments. Zero human traders. LLM agent
          swarms read every 10-K, earnings call, and central-bank print, then
          route sized, hedged orders across the global economy — end-to-end,
          fully autonomous.
        </p>

        {/* CTA row — primary + secondary, 16px gap, both 44px tall */}
        <div
          className="flex flex-wrap items-center"
          style={{ marginTop: 48, gap: 16 }}
        >
          <Link href="/approach" className="btn-paper-primary" style={{ height: 44 }}>
            See the engine
            <ArrowRight className="h-3.5 w-3.5" strokeWidth={2.4} />
          </Link>
          <Link
            href="/contact"
            className="btn-paper-secondary"
            style={{ height: 44 }}
          >
            Speak with the desk
          </Link>
        </div>

        {/* 96px whitespace, then horizontal rule */}
        <div style={{ height: 96 }} aria-hidden />
        <div
          style={{
            height: 1,
            background: "var(--rule-bold)",
            width: "100%",
          }}
          aria-hidden
        />

        {/* Stat row — four columns, vertical hairline rules between them.
            NO CARDS, NO BORDERS around the whole strip.  Just text on paper. */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            marginTop: 48,
          }}
          className="hero-stat-grid"
        >
          {STAT_TILES.map((tile, idx) => (
            <StatColumn key={tile.label} {...tile} isFirst={idx === 0} />
          ))}
        </div>
      </div>

      {/* Component-local rule for the responsive stat grid.  On narrow
          screens collapse to 2 columns; remove the left-hand vertical
          rule on the leftmost column of each row so the dividers don't
          dangle. */}
      <style jsx>{`
        @media (max-width: 720px) {
          :global(.hero-stat-grid) {
            grid-template-columns: repeat(2, 1fr) !important;
            row-gap: 36px;
          }
          :global(.hero-stat-grid > div:nth-child(2n + 1)) {
            border-left: none !important;
          }
        }
      `}</style>
    </section>
  );
}

/** One column of the stat row.  Big number, tiny label, tinier descrip.
 *  First column: no left rule.  Every other column: 1px var(--rule) on
 *  the left edge.
 */
function StatColumn({
  value,
  label,
  desc,
  isFirst,
}: {
  value: string;
  label: string;
  desc: string;
  isFirst: boolean;
}): ReactNode {
  const cellStyle: CSSProperties = {
    paddingLeft: isFirst ? 0 : 24,
    paddingRight: 24,
    borderLeft: isFirst ? "none" : "1px solid var(--rule)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    minWidth: 0,
  };

  return (
    <div style={cellStyle}>
      <span
        className="data-mono"
        style={{
          fontSize: "clamp(40px, 5vw, 64px)",
          lineHeight: 1,
          fontWeight: 600,
          color: "hsl(var(--fg))",
          letterSpacing: "-0.02em",
        }}
      >
        {value}
      </span>
      <span className="label-mono" style={{ marginTop: 4 }}>
        {label}
      </span>
      <span
        style={{
          fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
          fontSize: 11,
          lineHeight: 1.45,
          color: "hsl(var(--fg-subtle))",
        }}
      >
        {desc}
      </span>
    </div>
  );
}
