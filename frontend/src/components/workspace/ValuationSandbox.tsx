"use client";

import { useEffect, useState } from "react";
import { researchApi, type DCFResult } from "@/lib/research";

interface Props {
  baseRevenue?: number | null;
  baseLabel?: string;
  currency?: string | null;
  observedMargin?: number | null;
}

interface Slider {
  key: "revenue_growth_pct" | "operating_margin_pct" | "discount_rate_pct" | "terminal_growth_pct";
  label: string;
  min: number;
  max: number;
  step: number;
}

const SLIDERS: Slider[] = [
  { key: "revenue_growth_pct", label: "Revenue growth (yr)", min: -20, max: 60, step: 1 },
  { key: "operating_margin_pct", label: "Operating margin", min: 0, max: 80, step: 1 },
  { key: "discount_rate_pct", label: "Discount rate (WACC)", min: 5, max: 18, step: 0.5 },
  { key: "terminal_growth_pct", label: "Terminal growth", min: 0, max: 5, step: 0.5 },
];

const fmtBig = (v: string | null | undefined, ccy?: string | null) =>
  v == null ? "—" : `${Number(v) >= 1000 ? `${(Number(v) / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })}B` : `${Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}M`} ${ccy ?? ""}`;

/** Deterministic DCF computed server-side (Decimal). Every slider is ASSUMED; the base is OBSERVED. */
export function ValuationSandbox({ baseRevenue, baseLabel, currency, observedMargin }: Props) {
  const [p, setP] = useState({
    revenue_growth_pct: 15,
    operating_margin_pct: observedMargin ? Math.round(observedMargin) : 30,
    discount_rate_pct: 10,
    terminal_growth_pct: 3,
  });
  const [out, setOut] = useState<DCFResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!baseRevenue) return;
    const t = setTimeout(() => {
      researchApi
        .dcf({ base_revenue: baseRevenue, ...p, years: 5 })
        .then((r) => { setOut(r); setErr(null); })
        .catch((e: unknown) => setErr(e instanceof Error ? e.message : "Valuation failed"));
    }, 250);
    return () => clearTimeout(t);
  }, [baseRevenue, p]);

  if (!baseRevenue) {
    return <p className="ws-empty">No filing-derived revenue for this instrument — valuation sandbox needs an observed base.</p>;
  }
  return (
    <div className="ws-card">
      <p className="text-[12px] text-muted">
        <span className="ws-tag ws-tag-pos mr-2">Observed</span>Base revenue {fmtBig(String(baseRevenue), currency)} ({baseLabel})
        <span className="ws-tag ws-tag-warn mx-2">Assumed</span>all sliders below
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {SLIDERS.map((s) => (
          <label key={s.key} className="text-[12px]">
            <span className="flex justify-between text-muted"><span>{s.label}</span><span className="num text-fg">{p[s.key]}%</span></span>
            <input type="range" className="mt-1 w-full accent-[hsl(var(--brand))]" min={s.min} max={s.max} step={s.step}
                   value={p[s.key]} onChange={(e) => setP({ ...p, [s.key]: Number(e.target.value) })} aria-label={s.label} />
          </label>
        ))}
      </div>
      {err ? <p role="alert" className="mt-3 text-[13px] text-neg">{err}</p> : null}
      {out ? (
        <div className="mt-4" aria-live="polite">
          <div className="grid grid-cols-3 gap-3 text-[12px]">
            <div><p className="text-subtle">Enterprise value</p><p className="num text-[16px]">{fmtBig(out.result.enterprise_value, currency)}</p></div>
            <div><p className="text-subtle">PV explicit</p><p className="num">{fmtBig(out.result.pv_explicit, currency)}</p></div>
            <div><p className="text-subtle">Terminal share</p><p className="num">{out.result.terminal_share_pct}%</p></div>
          </div>
          <p className="mt-3 label-mono">Sensitivity — EV by discount rate × growth</p>
          <div className="ws-scroll-x">
            <table className="ws-table mt-1">
              <thead>
                <tr><th>WACC \ growth</th>{out.sensitivity.growth_rates.map((g) => <th key={g} className="text-right">{g}%</th>)}</tr>
              </thead>
              <tbody>
                {out.sensitivity.grid.map((row) => (
                  <tr key={row.discount_rate_pct}>
                    <td className="num">{row.discount_rate_pct}%</td>
                    {row.values.map((v, i) => <td key={i} className="num text-right">{fmtBig(v, null)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-subtle num">{out.result.formula}</p>
          <p className="mt-1 text-[11px] text-subtle">Scenario tool, not a price target. No share count is assumed, so output is enterprise value in reporting currency.</p>
        </div>
      ) : null}
    </div>
  );
}
