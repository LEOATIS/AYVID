"use client";

import { useEffect, useMemo, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/demoData";

/**
 * GraphPropagation — depth-feeling causal-graph render for the live-shock
 * console.  Themed entirely via CSS custom properties so it reads cleanly
 * in both light and dark mode.
 *
 * Design notes
 *   · Nodes are rendered as 3D-feeling orbs: each one is a `<radialGradient>`
 *     with a top-left highlight, mid-tone body, and rim-shadow → reads as a
 *     lit sphere instead of a flat disc.
 *   · Edges use a moving dashed `linearGradient` so propagation visibly
 *     "flows" from the shock outward, hop by hop.
 *   · Every text label sits inside a `graph-chip-bg` pill so it's always
 *     legible — never floating glyphs over crossing edges.
 *   · Concentric rings are *elliptical* (not circular) → the canvas reads
 *     as a top-down view of a plane, suggesting a third dimension.
 *   · A `hop-wave` keyframed pulse emanates from the shock node every cycle.
 */

type Props = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const RING_RX: Record<GraphNode["ring"], number> = {
  0: 0,
  1: 150,
  2: 260,
  3: 360,
  4: 440,
};
const RING_RY_RATIO = 0.78; // ellipses, not circles → faux perspective

type ClassKey = GraphNode["classification"];

const CLASS_HUE: Record<ClassKey, number> = {
  shock: 4,        // red
  beneficiary: 152, // green
  victim: 32,      // amber
  neutral: 220,    // blue-grey
};

/* ─────────────────────────────────────────────────────────────────
   Component
   ───────────────────────────────────────────────────────────────── */
export function GraphPropagation({ nodes, edges }: Props) {
  const [activeHop, setActiveHop] = useState(1);

  useEffect(() => {
    let h = 1;
    const id = setInterval(() => {
      h = h >= 3 ? 1 : h + 1;
      setActiveHop(h);
    }, 1800);
    return () => clearInterval(id);
  }, []);

  const positioned = useMemo(() => placeNodes(nodes), [nodes]);
  const positionMap = useMemo(
    () => new Map(positioned.map((n) => [n.id, n])),
    [positioned],
  );

  return (
    <section className="card overflow-hidden p-0">
      {/* ── Header ────────────────────────────────────────── */}
      <header className="flex flex-col gap-3 border-b border-app px-5 py-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h3 className="font-display text-[18px] font-medium tracking-tight text-fg">
            Causal propagation
          </h3>
          <p className="mt-0.5 text-[12px] text-muted">
            Neo4j supply-chain graph · 3-hop radius · live tier-2 walk
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[11px]">
          <Legend tone="shock" label="Shock" />
          <Legend tone="beneficiary" label="Beneficiary" />
          <Legend tone="victim" label="Victim" />
          <span className="hidden h-3 w-px bg-[hsl(var(--border-strong))] sm:inline-block" />
          <span className="text-muted">
            Hop wave{" "}
            <span className="num font-medium text-fg">{activeHop}</span>
            <span className="text-subtle"> / 3</span>
          </span>
        </div>
      </header>

      {/* ── Stage ─────────────────────────────────────────── */}
      <div className="graph-stage mx-3 my-4 sm:mx-4 sm:my-5">
        <svg
          viewBox="-420 -300 840 600"
          className="block h-auto w-full"
          role="img"
          aria-label="Causal-graph propagation showing the shock node at the center, tier-1 affected tickers on the inner ring, downstream victims on the middle ring, and indices on the outer ring."
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            {/* One radial gradient per classification — gives each node the
                lit-orb feel without needing per-node JS work. */}
            {(["shock", "beneficiary", "victim", "neutral"] as const).map(
              (cls) => (
                <radialGradient
                  key={cls}
                  id={`orb-${cls}`}
                  cx="35%"
                  cy="32%"
                  r="68%"
                >
                  <stop offset="0%" stopColor={`hsl(${CLASS_HUE[cls]} 90% 80%)`} />
                  <stop offset="55%" stopColor={`hsl(${CLASS_HUE[cls]} 78% 52%)`} />
                  <stop offset="100%" stopColor={`hsl(${CLASS_HUE[cls]} 70% 32%)`} />
                </radialGradient>
              ),
            )}

            {/* Animated edge gradient: a shimmer travels along the edge.
                We animate the x position so it sweeps left→right repeatedly. */}
            <linearGradient id="edge-flow" x1="0%" y1="0" x2="100%" y2="0">
              <stop offset="0%" stopColor="hsl(var(--info) / 0)" />
              <stop offset="50%" stopColor="hsl(var(--info))" stopOpacity="1">
                <animate
                  attributeName="offset"
                  values="0;1"
                  dur="2.2s"
                  repeatCount="indefinite"
                />
              </stop>
              <stop offset="100%" stopColor="hsl(var(--info) / 0)" />
            </linearGradient>

            {/* Soft glow filter shared by all orbs (works in both themes
                because it just blurs the source colour) */}
            <filter id="orb-glow" x="-60%" y="-60%" width="220%" height="220%">
              <feGaussianBlur stdDeviation="6" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>

            {/* Drop shadow that grounds the orb on the imaginary plane */}
            <filter id="orb-shadow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur in="SourceAlpha" stdDeviation="4" />
              <feOffset dy="6" result="o" />
              <feComponentTransfer>
                <feFuncA type="linear" slope="0.35" />
              </feComponentTransfer>
              <feMerge>
                <feMergeNode />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* ── Perspective rings ─────────────────────────────── */}
          <g className="graph-ring-stroke" fill="none" strokeDasharray="3 6">
            {[1, 2, 3, 4].map((r) => (
              <ellipse
                key={r}
                cx={0}
                cy={0}
                rx={RING_RX[r as 1 | 2 | 3]}
                ry={RING_RX[r as 1 | 2 | 3] * RING_RY_RATIO}
              />
            ))}
          </g>

          {/* ── Edges ──────────────────────────────────────── */}
          <g>
            {edges.map((e) => {
              const a = positionMap.get(e.from);
              const b = positionMap.get(e.to);
              if (!a || !b) return null;
              const isActive = activeHop >= e.hop;
              return (
                <EdgeLine
                  key={e.id}
                  ax={a.x}
                  ay={a.y}
                  bx={b.x}
                  by={b.y}
                  weight={e.weight}
                  active={isActive}
                  label={e.label}
                />
              );
            })}
          </g>

          {/* ── Nodes ──────────────────────────────────────── */}
          <g>
            {positioned.map((n) => (
              <Orb key={n.id} node={n} />
            ))}
          </g>
        </svg>
      </div>

      {/* ── Footer / explanatory caption ──────────────────── */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-app bg-[hsl(var(--bg-elev))] px-5 py-3 text-[11px] text-muted">
        <span>
          <span className="font-medium text-fg">Tier 1</span> ≤ 30 s — direct
          causal chain
        </span>
        <span className="text-subtle">·</span>
        <span>
          <span className="font-medium text-fg">Tier 2</span> ≈ 120 s — topology
          walk
        </span>
        <span className="text-subtle">·</span>
        <span>
          <span className="font-medium text-fg">Tier 3</span> ≈ 5 min — re-priced
          signal book
        </span>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Sub-components
   ───────────────────────────────────────────────────────────────── */

function Legend({ tone, label }: { tone: ClassKey; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{
          background: `hsl(${CLASS_HUE[tone]} 78% 52%)`,
          boxShadow: `0 0 0 3px hsl(${CLASS_HUE[tone]} 78% 52% / 0.18)`,
        }}
      />
      <span className="text-fg">{label}</span>
    </span>
  );
}

function EdgeLine({
  ax,
  ay,
  bx,
  by,
  weight,
  active,
  label,
}: {
  ax: number;
  ay: number;
  bx: number;
  by: number;
  weight: number;
  active: boolean;
  label?: string;
}) {
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const labelW = (label?.length ?? 0) * 5.4 + 14;
  return (
    <g>
      {/* Inactive base path is always drawn, so the topology never disappears */}
      <line
        x1={ax}
        y1={ay}
        x2={bx}
        y2={by}
        className="graph-edge-inactive"
        strokeWidth={1}
      />
      {active && (
        <line
          x1={ax}
          y1={ay}
          x2={bx}
          y2={by}
          stroke="url(#edge-flow)"
          strokeWidth={1.4 + weight * 1.6}
          strokeDasharray="6 6"
          strokeLinecap="round"
          opacity={0.95}
          style={{ animation: "flow-dash 1.4s linear infinite" }}
        />
      )}
      {active && label && (
        <g transform={`translate(${mx}, ${my - 2})`} aria-hidden>
          <rect
            x={-labelW / 2}
            y={-7}
            width={labelW}
            height={14}
            rx={7}
            className="graph-chip-bg"
            strokeWidth={0.75}
          />
          <text
            y={3}
            textAnchor="middle"
            fontSize={9}
            className="graph-chip-text-muted"
            style={{ fontFamily: "var(--font-mono), ui-monospace, monospace" }}
          >
            {label}
          </text>
        </g>
      )}
    </g>
  );
}

function Orb({ node }: { node: Positioned }) {
  const isShock = node.ring === 0;
  const r = isShock ? 28 : 16;
  const labelW = Math.max(node.label.length * 6.2 + 14, 36);
  const deltaTxt = `${node.delta >= 0 ? "+" : ""}${(node.delta * 100).toFixed(2)}%`;
  return (
    <g transform={`translate(${node.x}, ${node.y})`}>
      {/* Hop wave — only on the shock node */}
      {isShock && (
        <>
          <circle
            r={28}
            className="hop-wave graph-ring-stroke"
            style={{ stroke: `hsl(${CLASS_HUE.shock} 78% 52% / 0.85)`, strokeWidth: 1.5 }}
          />
          <circle
            r={28}
            className="hop-wave graph-ring-stroke"
            style={{
              stroke: `hsl(${CLASS_HUE.shock} 78% 52% / 0.85)`,
              strokeWidth: 1.5,
              animationDelay: "0.9s",
            }}
          />
        </>
      )}

      {/* Soft halo behind the orb (uses the classification colour) */}
      <circle
        r={r * 1.6}
        fill={`hsl(${CLASS_HUE[node.classification]} 78% 52% / 0.18)`}
        filter="url(#orb-glow)"
      />

      {/* The orb itself */}
      <circle
        r={r}
        fill={`url(#orb-${node.classification})`}
        stroke={`hsl(${CLASS_HUE[node.classification]} 78% 35% / 0.9)`}
        strokeWidth={1.2}
        filter="url(#orb-shadow)"
      />
      {/* Top-left specular highlight to sell the 3D feel */}
      <ellipse
        cx={-r * 0.32}
        cy={-r * 0.4}
        rx={r * 0.42}
        ry={r * 0.22}
        fill="white"
        opacity={0.45}
      />

      {/* Ticker label inside a chip */}
      <g transform={`translate(0, ${r + 16})`}>
        <rect
          x={-labelW / 2}
          y={-9}
          width={labelW}
          height={18}
          rx={9}
          className="graph-chip-bg"
          strokeWidth={0.75}
        />
        <text
          y={-1}
          textAnchor="middle"
          fontSize={11}
          fontWeight={600}
          className="graph-chip-text-fg"
          style={{ fontFamily: "var(--font-sans), system-ui, sans-serif" }}
        >
          {node.label}
        </text>
        <text
          y={9}
          textAnchor="middle"
          fontSize={9}
          className={node.delta >= 0 ? "graph-chip-text-pos" : "graph-chip-text-neg"}
          style={{ fontFamily: "var(--font-mono), ui-monospace, monospace" }}
        >
          {deltaTxt}
        </text>
      </g>
    </g>
  );
}

/* ─────────────────────────────────────────────────────────────────
   Layout helpers
   ───────────────────────────────────────────────────────────────── */
type Positioned = GraphNode & { x: number; y: number };

function placeNodes(nodes: GraphNode[]): Positioned[] {
  const groups: Record<number, GraphNode[]> = { 0: [], 1: [], 2: [], 3: [], 4: [] };
  for (const n of nodes) groups[n.ring] ??= [], groups[n.ring].push(n);

  const out: Positioned[] = [];
  for (const ringStr of Object.keys(groups)) {
    const ring = Number(ringStr) as 0 | 1 | 2 | 3 | 4;
    const arr = groups[ring];
    if (ring === 0) {
      arr.forEach((n) => out.push({ ...n, x: 0, y: 0 }));
      continue;
    }
    const rx = RING_RX[ring];
    const ry = rx * RING_RY_RATIO;
    // Stagger ring offsets so nodes don't stack vertically across rings,
    // and so the layout reads as a 3-deep onion rather than a wheel.
    const offset = ring === 1 ? -Math.PI / 2 : ring === 2 ? -Math.PI / 2 + Math.PI / arr.length : -Math.PI / 2 + Math.PI / (2 * arr.length);
    arr.forEach((n, i) => {
      const angle = offset + (i / arr.length) * Math.PI * 2;
      out.push({
        ...n,
        x: Math.cos(angle) * rx,
        y: Math.sin(angle) * ry,
      });
    });
  }
  return out;
}
