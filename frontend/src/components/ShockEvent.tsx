"use client";

import { useEffect, useRef, useState } from "react";
import {
  motion, useMotionValue, useSpring, useTransform,
} from "motion/react";
import type { ShockEvent } from "@/lib/demoData";

/**
 * ShockEventCard — the "Live Shock Detected" header on the
 * live-shock page. Designed to read as *actively in motion* without
 * being distracting, using five coordinated motion channels:
 *
 *   1. Entry — the whole card slides up + fades in on mount / replay
 *   2. Label shimmer — a brand-coloured gradient sweeps across
 *      "LIVE SHOCK DETECTED" every ~3s
 *   3. Dot pulse — radial ping + inner breathing glow
 *   4. Count-up — Magnitude / Confidence values tween from 0 to
 *      target using a physical spring (the finance-dashboard tell)
 *   5. Causal chain typewriter — characters reveal 12ms apart with
 *      a blinking caret, evoking the LLM composing the chain
 *
 * The ``event.id`` dependency on each animation makes all five
 * channels restart in lockstep whenever the user picks a new
 * scenario or hits Replay.
 */

const CATEGORY_LABEL: Record<ShockEvent["category"], string> = {
  supply_disruption: "Supply Disruption",
  tariff_escalation: "Tariff Escalation",
  rate_decision: "Rate Decision",
  geopolitical: "Geopolitical",
  earnings: "Earnings Surprise",
  macro_print: "Macro Print",
};

export function ShockEventCard({ event }: { event: ShockEvent }) {
  const negative = event.magnitude < 0;
  const dotBg = negative ? "bg-rose-400" : "bg-emerald-400";
  const dotText = negative ? "text-rose-300" : "text-emerald-300";
  const magnitudeText = negative ? "text-rose-300" : "text-emerald-300";

  return (
    <motion.section
      key={event.id}
      initial={{ opacity: 0, y: 12, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
      className="card relative overflow-hidden p-5"
    >
      <div className="flex flex-col items-start justify-between gap-6 lg:flex-row">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <PulsingDot dotBg={dotBg} />
            <ShimmerLabel tone={dotText}>Live Shock Detected</ShimmerLabel>
            <span className="text-[11px] text-subtle">
              · {CATEGORY_LABEL[event.category]}
            </span>
          </div>
          <motion.h2
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: 0.08 }}
            className="mt-2 max-w-3xl text-lg font-semibold leading-snug text-fg"
          >
            {event.headline}
          </motion.h2>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4, delay: 0.18 }}
            className="mt-2 text-xs text-muted"
          >
            {event.source}
          </motion.p>
        </div>
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.12 }}
          className="grid grid-cols-3 gap-3 text-right"
        >
          <CountMetric
            label="Magnitude"
            target={event.magnitude * 100}
            formatter={(v) => `${v.toFixed(2)}%`}
            valueClass={magnitudeText}
          />
          <CountMetric
            label="Confidence"
            target={event.confidence * 100}
            formatter={(v) => `${v.toFixed(0)}%`}
            valueClass="text-cyan-300"
          />
          <TimeMetric
            label="Detected"
            value={new Date(event.detectedAt).toUTCString().slice(17, 22)}
          />
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.4, delay: 0.22 }}
        className="mt-4 rounded-lg border border-app bg-[hsl(var(--bg-sunken))] p-3 text-[12px] leading-relaxed text-muted"
      >
        <span className="text-[10px] uppercase tracking-[0.18em] text-subtle">
          Causal Chain (LLM-extracted)
        </span>
        <Typewriter
          key={event.id}
          text={event.causalChain}
          className="num mt-1 block text-[11.5px] text-fg"
        />
      </motion.div>

      {/* Ambient top-edge sweep — ties visually to the pipeline bar's
          own sweep so the "live" feel is coherent across the page. */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden"
      >
        <motion.span
          className="block h-full w-1/3"
          style={{
            background:
              "linear-gradient(90deg, transparent, hsl(var(--brand) / 0.9), transparent)",
          }}
          animate={{ x: ["-33%", "400%"] }}
          transition={{ duration: 3.4, repeat: Infinity, ease: "linear" }}
        />
      </span>
    </motion.section>
  );
}

/* ─── Dot with a visible pulse ring ───────────────────────────── */

function PulsingDot({ dotBg }: { dotBg: string }) {
  return (
    <span className="relative inline-flex h-2.5 w-2.5 items-center justify-center">
      {/* Outer radial ping — scale + fade, once every 1.5s */}
      <motion.span
        aria-hidden
        className={`absolute inline-block rounded-full ${dotBg}`}
        style={{ width: "100%", height: "100%" }}
        animate={{ scale: [1, 2.6], opacity: [0.75, 0] }}
        transition={{ duration: 1.5, repeat: Infinity, ease: "easeOut" }}
      />
      {/* Inner breathing glow */}
      <motion.span
        aria-hidden
        className={`absolute inline-block rounded-full ${dotBg}`}
        style={{ width: "100%", height: "100%", filter: "blur(3px)" }}
        animate={{ opacity: [0.45, 0.85, 0.45] }}
        transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* Solid core */}
      <span className={`relative inline-block h-full w-full rounded-full ${dotBg}`} />
    </span>
  );
}

/* ─── Label with a brand-coloured shimmer sweeping across ───── */

function ShimmerLabel({
  tone, children,
}: { tone: string; children: React.ReactNode }) {
  return (
    <span
      className={`relative inline-block overflow-hidden text-[11px] font-semibold uppercase tracking-[0.18em] ${tone}`}
      style={{
        backgroundImage:
          "linear-gradient(90deg, currentColor 0%, currentColor 40%, hsl(var(--brand) / 1) 50%, currentColor 60%, currentColor 100%)",
        backgroundSize: "250% 100%",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        // Keeps legible colour while the gradient sweeps across.
        color: "transparent",
        WebkitTextFillColor: "transparent",
        animation: "ayv-shimmer 2.8s linear infinite",
      }}
    >
      {children}
      <style jsx>{`
        @keyframes ayv-shimmer {
          0%   { background-position: 250% 0; }
          100% { background-position: -150% 0; }
        }
      `}</style>
    </span>
  );
}

/* ─── Animated counter — spring-tweened number ─────────────── */

function CountMetric({
  label, target, formatter, valueClass,
}: {
  label: string;
  target: number;
  formatter: (v: number) => string;
  valueClass: string;
}) {
  const mv = useMotionValue(0);
  const spring = useSpring(mv, { stiffness: 110, damping: 22, mass: 0.9 });
  const rendered = useTransform(spring, (v) => formatter(v));
  const [display, setDisplay] = useState(formatter(0));

  useEffect(() => {
    mv.set(0);
    // Tiny delay so the card's entry animation and counters don't
    // race on the same frame.
    const id = setTimeout(() => mv.set(target), 80);
    const unsub = rendered.on("change", setDisplay);
    return () => {
      clearTimeout(id);
      unsub();
    };
  }, [target, mv, rendered, formatter]);

  return (
    <div className="rounded-lg border border-app bg-[hsl(var(--bg-sunken))] px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-subtle">
        {label}
      </div>
      <div className={`num mt-0.5 text-base font-semibold tabular-nums ${valueClass}`}>
        {display}
      </div>
    </div>
  );
}

function TimeMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-app bg-[hsl(var(--bg-sunken))] px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-subtle">
        {label}
      </div>
      <motion.div
        initial={{ opacity: 0, letterSpacing: "0.4em" }}
        animate={{ opacity: 1, letterSpacing: "0em" }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="num mt-0.5 text-base font-semibold tabular-nums text-white/85"
      >
        {value}
      </motion.div>
    </div>
  );
}

/* ─── Typewriter reveal — LLM composing the chain ──────────── */

function Typewriter({
  text, className, cps = 85,
}: { text: string; className?: string; cps?: number }) {
  const [shown, setShown] = useState(0);
  const raf = useRef<number | null>(null);
  const start = useRef<number | null>(null);

  useEffect(() => {
    setShown(0);
    start.current = null;
    const tick = (t: number) => {
      if (start.current === null) start.current = t;
      const elapsed = (t - start.current) / 1000;
      const n = Math.min(text.length, Math.floor(elapsed * cps));
      setShown(n);
      if (n < text.length) {
        raf.current = requestAnimationFrame(tick);
      }
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [text, cps]);

  const done = shown >= text.length;

  return (
    <p className={className}>
      {text.slice(0, shown)}
      <span
        aria-hidden
        className={`ml-[1px] inline-block w-[6px] translate-y-[1px] border-b border-current ${
          done ? "opacity-0" : "animate-[ayv-caret_0.9s_steps(2)_infinite]"
        }`}
      >
        &nbsp;
      </span>
      <style jsx>{`
        @keyframes ayv-caret {
          0%, 49%   { opacity: 1; }
          50%, 100% { opacity: 0; }
        }
      `}</style>
    </p>
  );
}
