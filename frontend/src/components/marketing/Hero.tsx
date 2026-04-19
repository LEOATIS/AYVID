"use client";

import Link from "next/link";
import { useRef, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { HeroVisual } from "@/components/marketing/HeroVisual";
import { HeroShowcase } from "@/components/marketing/HeroShowcase";

/**
 * AYVID Hero — strict design system.
 *
 *   • Type scale  : Golden Ratio (φ = 1.618).  H1 clamps 68→110.
 *   • Spacing     : Fibonacci ONLY  (4 / 8 / 13 / 21 / 34 / 55 / 89 / 144).
 *   • Reveal      : Monument curve  cubic-bezier(0.22, 1, 0.36, 1) — pure
 *                   CSS keyframe so the page is visible pre-hydration.
 *   • Primary CTA : Cursor-tracking gold glow border (no flash).
 *   • Layout      : 12-col grid, max prose 65ch, no overflow at any width.
 */
export function Hero() {
  return (
    <section className="relative isolate overflow-hidden">
      {/* Ambient glow — gold @ 5% on dark, slate-blue @ 40% on light. */}
      <div className="hero-glow" aria-hidden />
      <div className="mesh-grain pointer-events-none absolute inset-0" aria-hidden />

      <div
        className="container-page relative z-10"
        style={{
          paddingTop: "var(--s-6)",
          paddingBottom: "var(--s-7)",
        }}
      >
        {/* ───────────────── Hero copy — left-aligned, capped width
            (asymmetry inside the central column, not via 12-col split). */}
        <div className="max-w-[1080px]" style={{ minWidth: 0 }}>
          <Reveal delay={0}>
            <span className="eyebrow-pill">
              <span className="sparkle" aria-hidden>
                <Sparkles className="h-3 w-3" strokeWidth={2.6} />
              </span>
              AYVID&nbsp;·&nbsp;AI-Native Hedge Fund
            </span>
          </Reveal>

          <Reveal delay={60}>
            <h1
              className="wordmark-polo"
              style={{ marginTop: "var(--s-5)" }}
            >
              <span className="tone-bright">Intelligent</span>
              <br />
              <span className="tone-mute">Decisions.</span>
            </h1>
          </Reveal>

          <Reveal delay={120}>
            <p
              className="eyebrow-wide"
              style={{ marginTop: "var(--s-4)" }}
            >
              <b>A</b>symmetric&nbsp;&nbsp;<b>Y</b>ield&nbsp;&nbsp;
              <b>V</b>ia&nbsp;&nbsp;<b>I</b>ntelligent&nbsp;&nbsp;
              <b>D</b>ecisions
            </p>
          </Reveal>

          <Reveal delay={180}>
            <p
              className="text-fg-muted"
              style={{
                marginTop: "var(--s-5)",
                maxWidth: "65ch",
                fontSize: "var(--t-body)",
                lineHeight: "var(--lh-body)",
              }}
            >
              An AI-native hedge fund. Swarms of LLM agents read every 10-K,
              earnings call, and SEC filing in real time, trace the
              consequences through a live causal graph of the global economy,
              and size, hedge, and route the trades —{" "}
              <strong className="text-fg">end-to-end, no humans on the trade path</strong>.
            </p>
          </Reveal>

          <Reveal delay={260}>
            <div
              className="flex flex-wrap items-center"
              style={{ marginTop: "var(--s-6)", gap: "var(--s-3)" }}
            >
              <GlowCTA href="/approach">
                See the engine
                <span className="arrow" aria-hidden>
                  <ArrowRight />
                </span>
              </GlowCTA>
              <Link href="/contact" className="btn-polo btn-polo-paper">
                Speak with the desk
                <span className="arrow" aria-hidden>
                  <ArrowRight />
                </span>
              </Link>
            </div>
          </Reveal>

          <Reveal delay={340}>
            <div style={{ marginTop: "var(--s-6)" }}>
              <div className="proof-bar">
                <span>
                  <span className="dot" aria-hidden />
                  <b>Live</b>&nbsp;· IB Paper
                </span>
                <span>
                  <b>+43.6%</b>&nbsp;walk-forward 2018&nbsp;→&nbsp;2025
                </span>
                <span>
                  <b>Neo4j</b>&nbsp;· causal graph
                </span>
              </div>
            </div>
          </Reveal>
        </div>

        {/* ───────────────── Showcase preview — full width below the fold */}
        <Reveal delay={420}>
          <div
            className="mx-auto"
            style={{ marginTop: "var(--s-7)", maxWidth: "1080px" }}
          >
            <HeroShowcase>
              <HeroVisual />
            </HeroShowcase>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Pure-CSS monument reveal — slide-up + fade. The element is fully
   visible at end-state so SSR has no FOUC.  `delay` in ms.
   ───────────────────────────────────────────────────────────────── */
function Reveal({
  children,
  delay = 0,
}: {
  children: ReactNode;
  delay?: number;
}) {
  const style: CSSProperties = { animationDelay: `${delay}ms` };
  return (
    <div className="reveal-up" style={style}>
      {children}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Cursor-tracking glow CTA — sets --mx / --my CSS vars on move so
   the radial gold ring follows the pointer.  No flash; pure CSS.
   ───────────────────────────────────────────────────────────────── */
function GlowCTA({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLAnchorElement | null>(null);

  const onMove = (e: MouseEvent<HTMLAnchorElement>) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    el.style.setProperty("--mx", `${x}%`);
    el.style.setProperty("--my", `${y}%`);
  };

  return (
    <Link
      ref={ref}
      href={href}
      onMouseMove={onMove}
      className="btn-polo btn-polo-ink btn-polo-glow"
    >
      {children}
    </Link>
  );
}
