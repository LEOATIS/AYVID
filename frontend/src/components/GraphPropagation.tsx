"use client";

import { useEffect, useMemo, useState } from "react";
import type { GraphEdge, GraphNode } from "@/lib/demoData";

type Props = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const RING_RADIUS: Record<GraphNode["ring"], number> = {
  0: 0,
  1: 130,
  2: 240,
  3: 340,
};

const CLASS_FILL: Record<GraphNode["classification"], string> = {
  shock: "#ff453a",
  beneficiary: "#30d158",
  victim: "#ff9f0a",
  neutral: "#8e8e93",
};

const CLASS_GLOW: Record<GraphNode["classification"], string> = {
  shock: "rgba(255,69,58,0.55)",
  beneficiary: "rgba(48,209,88,0.55)",
  victim: "rgba(255,159,10,0.55)",
  neutral: "rgba(142,142,147,0.30)",
};

export function GraphPropagation({ nodes, edges }: Props) {
  const [activeHop, setActiveHop] = useState(0);

  useEffect(() => {
    let h = 0;
    const id = setInterval(() => {
      h = h >= 3 ? 0 : h + 1;
      setActiveHop(h);
    }, 1400);
    return () => clearInterval(id);
  }, []);

  const positioned = useMemo(() => placeNodes(nodes), [nodes]);
  const positionMap = useMemo(
    () => new Map(positioned.map((n) => [n.id, n])),
    [positioned]
  );

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white/90">
            Causal Propagation
          </h3>
          <p className="text-[11px] text-white/45">
            Neo4j supply-chain graph · 3-hop radius · live tier-2 walk
          </p>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-white/55">
          <Legend swatch="#ff453a" label="Shock" />
          <Legend swatch="#30d158" label="Beneficiary" />
          <Legend swatch="#ff9f0a" label="Victim" />
        </div>
      </header>

      <svg viewBox="-380 -280 760 560" className="w-full">
        <defs>
          <radialGradient id="ringFade" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(10,132,255,0)" />
            <stop offset="80%" stopColor="rgba(10,132,255,0.10)" />
            <stop offset="100%" stopColor="rgba(10,132,255,0)" />
          </radialGradient>
        </defs>

        {[1, 2, 3].map((r) => (
          <circle
            key={r}
            cx={0}
            cy={0}
            r={RING_RADIUS[r as 1 | 2 | 3]}
            fill="none"
            stroke="rgba(255,255,255,0.06)"
            strokeDasharray="3 5"
          />
        ))}

        {edges.map((e) => {
          const a = positionMap.get(e.from);
          const b = positionMap.get(e.to);
          if (!a || !b) return null;
          const isActive = activeHop >= e.hop;
          return (
            <g key={e.id}>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={isActive ? "#0a84ff" : "rgba(255,255,255,0.10)"}
                strokeWidth={isActive ? 1.6 + e.weight * 1.2 : 0.8}
                strokeDasharray={isActive ? "8 6" : "none"}
                opacity={isActive ? 0.85 : 0.5}
                style={
                  isActive
                    ? { animation: "flow-dash 1.2s linear infinite" }
                    : undefined
                }
              />
              {isActive && (
                <text
                  x={(a.x + b.x) / 2}
                  y={(a.y + b.y) / 2 - 4}
                  fontSize={9}
                  fill="rgba(255,255,255,0.55)"
                  textAnchor="middle"
                >
                  {e.label}
                </text>
              )}
            </g>
          );
        })}

        {positioned.map((n) => {
          const r = n.ring === 0 ? 22 : 14;
          return (
            <g key={n.id} transform={`translate(${n.x}, ${n.y})`}>
              {n.ring === 0 && (
                <circle
                  r={r + 8}
                  fill="none"
                  stroke={CLASS_FILL[n.classification]}
                  strokeWidth={1.2}
                  className="pulse-ring"
                  style={{ transformOrigin: "center" }}
                />
              )}
              <circle
                r={r}
                fill={CLASS_FILL[n.classification]}
                stroke="rgba(0,0,0,0.6)"
                strokeWidth={1.5}
                style={{ filter: `drop-shadow(0 0 8px ${CLASS_GLOW[n.classification]})` }}
              />
              <text
                y={r + 14}
                textAnchor="middle"
                fontSize={10.5}
                fill="rgba(255,255,255,0.85)"
                fontWeight={600}
              >
                {n.label}
              </text>
              <text
                y={r + 26}
                textAnchor="middle"
                fontSize={9}
                fill={n.delta >= 0 ? "#30d158" : "#ff453a"}
              >
                {(n.delta * 100).toFixed(2)}%
              </text>
            </g>
          );
        })}
      </svg>

      <div className="mt-3 flex items-center justify-between text-[11px] text-white/45">
        <span>
          Hop wave:{" "}
          <span className="font-mono text-white/80">{activeHop || "—"}</span> /
          3
        </span>
        <span>
          Tier 1 (≤30s): inferred from causal chain · Tier 2 (~120s): topology
          walk · Tier 3 (~5m): re-priced signal book
        </span>
      </div>
    </section>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{ background: swatch }}
      />
      {label}
    </span>
  );
}

type Positioned = GraphNode & { x: number; y: number };

function placeNodes(nodes: GraphNode[]): Positioned[] {
  const groups: Record<number, GraphNode[]> = { 0: [], 1: [], 2: [], 3: [] };
  for (const n of nodes) groups[n.ring].push(n);

  const out: Positioned[] = [];
  for (const ringStr of Object.keys(groups)) {
    const ring = Number(ringStr) as 0 | 1 | 2 | 3;
    const arr = groups[ring];
    if (ring === 0) {
      arr.forEach((n) => out.push({ ...n, x: 0, y: 0 }));
      continue;
    }
    const radius = RING_RADIUS[ring];
    const offset = ring === 2 ? Math.PI / arr.length : 0;
    arr.forEach((n, i) => {
      const angle = (i / arr.length) * Math.PI * 2 + offset;
      out.push({
        ...n,
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      });
    });
  }
  return out;
}
