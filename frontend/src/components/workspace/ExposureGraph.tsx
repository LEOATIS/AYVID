"use client";

import type { Exposure } from "@/lib/research";

interface Props {
  rows: Exposure[];
  origins: string[];
  onSelect: (edge: Exposure) => void;
}

const COL_W = 150;
const ROW_H = 34;

/** Layered propagation graph: upstream ← origin → 1st → 2nd → 3rd order. */
export function ExposureGraph({ rows, origins, onSelect }: Props) {
  const column = new Map<string, number>();
  origins.forEach((o) => column.set(o, 1));
  rows.filter((r) => r.direction === "UPSTREAM").forEach((r) => !column.has(r.from) && column.set(r.from, 0));
  rows
    .filter((r) => r.direction === "DOWNSTREAM")
    .sort((a, b) => a.order - b.order)
    .forEach((r) => {
      if (!column.has(r.from)) column.set(r.from, r.order);
      if (!column.has(r.to)) column.set(r.to, r.order + 1);
    });
  const cols = new Map<number, string[]>();
  column.forEach((c, sym) => cols.set(c, [...(cols.get(c) ?? []), sym]));
  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((syms, c) => syms.forEach((s, i) => pos.set(s, { x: 60 + c * COL_W, y: 28 + i * ROW_H })));
  const height = Math.max(...[...cols.values()].map((s) => s.length), 1) * ROW_H + 40;
  const width = 60 + (Math.max(...cols.keys(), 1) + 1) * COL_W;
  const headers = ["Upstream", "Origin", "1st order", "2nd order", "3rd order"];

  return (
    <div className="ws-scroll-x">
      <svg width={width} height={height} role="group" aria-label="Supply-chain propagation graph — the table view lists the same edges" className="block">
        {headers.slice(0, Math.max(...cols.keys(), 1) + 1).map((h, i) => (
          <text key={h} x={60 + i * COL_W} y={12} textAnchor="middle" className="graph-chip-text-subtle" fontSize={10} letterSpacing="0.08em">
            {h.toUpperCase()}
          </text>
        ))}
        {rows.map((r) => {
          const a = pos.get(r.from);
          const b = pos.get(r.to);
          if (!a || !b) return null;
          const dashed = r.evidence_status !== "EXTRACTED";
          return (
            <g key={r.edge_id} className="cursor-pointer" onClick={() => onSelect(r)} role="button" tabIndex={0}
               aria-label={`${r.from} to ${r.to}, ${r.relationship_type}, ${r.evidence_status}`}
               onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(r)}>
              <line x1={a.x + 30} y1={a.y} x2={b.x - 30} y2={b.y} stroke="hsl(var(--border-strong))"
                    strokeWidth={1 + 3 * Math.min(r.exposure_score * 4, 1)} strokeDasharray={dashed ? "4 3" : undefined} />
              <line x1={a.x + 30} y1={a.y} x2={b.x - 30} y2={b.y} stroke="transparent" strokeWidth={12} />
            </g>
          );
        })}
        {[...pos.entries()].map(([sym, p]) => {
          const isOrigin = origins.includes(sym);
          return (
            <g key={sym} transform={`translate(${p.x - 30},${p.y - 11})`}>
              <rect width={60} height={22} rx={3} fill={isOrigin ? "hsl(var(--fg))" : "hsl(var(--panel))"} stroke={isOrigin ? "hsl(var(--brand))" : "hsl(var(--border-strong))"} />
              <text x={30} y={15} textAnchor="middle" fontSize={11} fontFamily="var(--font-mono)" fill={isOrigin ? "hsl(var(--bg))" : "hsl(var(--fg))"}>
                {sym.length > 8 ? `${sym.slice(0, 7)}…` : sym}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-[11px] text-subtle">Solid = extracted from a filing · dashed = inferred/curated · width = dependency × criticality.</p>
    </div>
  );
}
