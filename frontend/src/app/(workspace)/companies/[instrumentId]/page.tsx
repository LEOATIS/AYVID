"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiNotFoundError } from "@/lib/api";
import { fmtValue, useCompany, usePrices, type CompanyPayload, type DataRow, type EdgeRow } from "@/lib/research";
import { useEvidence } from "@/components/workspace/EvidenceDrawer";
import { HealthDot } from "@/components/workspace/WorkspaceShell";
import { ValuationSandbox } from "@/components/workspace/ValuationSandbox";

type Obs = CompanyPayload["observations"][number];

const FIN_METRICS = ["REVENUE", "GROSS_PROFIT", "GROSS_MARGIN_PCT", "OPERATING_INCOME", "OPERATING_MARGIN_PCT", "NET_INCOME", "NET_MARGIN_PCT", "REVENUE_GROWTH_PCT"];
const MARKET_METRICS = ["CLOSE_PRICE", "RETURN_90D_PCT", "REALIZED_VOL_ANN", "MAX_DRAWDOWN_PCT", "HURST_EXPONENT"];

function toRow(o: Obs, symbol: string, iid: string, stale: boolean): DataRow {
  return {
    instrument_id: iid, symbol, metric_code: o.metric_code, metric_label: o.metric_label, fiscal_period: o.fiscal_period,
    value: o.value, display: fmtValue(o.value, o.scale, o.currency), currency: o.currency, scale: o.scale, basis: o.basis,
    status: o.status, as_of: o.as_of, is_stale: stale, age_days: null, ref: null, source_label: null,
    observation_id: o.observation_id, epistemic: o.status === "DERIVED" ? "CALCULATED" : "REPORTED",
  };
}

function useInspect(symbol: string, iid: string) {
  const { open } = useEvidence();
  return (o: Obs, stale = false) => {
    const row = toRow(o, symbol, iid, stale);
    if (o.status === "DERIVED" && o.formula) {
      const inputs = typeof o.inputs === "string" ? JSON.parse(o.inputs) : o.inputs;
      open({
        tab: "calc", label: o.metric_label, row, sourceId: o.source_id,
        calc: { label: "CALC", label_text: `${symbol} ${o.metric_label} (${o.fiscal_period})`, formula: o.formula, inputs: inputs ?? [],
                result: o.value, unit: o.scale === "PERCENT" ? "%" : o.scale.toLowerCase(), observation_id: o.observation_id,
                source_label: null, instrument_id: iid, as_of: o.as_of },
      });
    } else {
      open({ tab: o.source_id ? "source" : "provenance", label: o.metric_label, row, sourceId: o.source_id });
    }
  };
}

function Financials({ data }: { data: CompanyPayload }) {
  const router = useRouter();
  const params = useSearchParams();
  const period = params.get("period") === "quarterly" ? "quarterly" : "annual";
  const inspect = useInspect(data.instrument.symbol, data.instrument.instrument_id);
  const obs = data.observations.filter(
    (o) => FIN_METRICS.includes(o.metric_code) && (period === "annual" ? o.fiscal_period.startsWith("FY") : o.fiscal_period.startsWith("Q")),
  );
  const periods = [...new Set(obs.map((o) => o.fiscal_period))].sort();
  const setPeriod = (p: string) => router.replace(`?period=${p}`, { scroll: false });
  return (
    <section className="ws-section" aria-labelledby="fin">
      <div className="flex items-baseline justify-between">
        <h2 id="fin" className="ws-h2">Financials (income statement)</h2>
        <div className="ws-seg" role="group" aria-label="Period">
          <button type="button" aria-pressed={period === "annual"} onClick={() => setPeriod("annual")}>Annual</button>
          <button type="button" aria-pressed={period === "quarterly"} onClick={() => setPeriod("quarterly")}>Quarterly</button>
        </div>
      </div>
      {!periods.length ? (
        <p className="ws-empty">
          No {period} filing-derived figures in AYVID&apos;s dataset for this instrument. Balance-sheet and cash-flow statements are not yet ingested.
        </p>
      ) : (
        <div className="ws-scroll-x">
          <table className="ws-table">
            <thead><tr><th>Metric</th>{periods.map((p) => <th key={p} className="text-right">{p}</th>)}</tr></thead>
            <tbody>
              {FIN_METRICS.map((code) => {
                const cells = periods.map((p) => obs.find((o) => o.metric_code === code && o.fiscal_period === p));
                if (cells.every((c) => !c)) return null;
                const label = cells.find(Boolean)?.metric_label;
                return (
                  <tr key={code}>
                    <td>{label}</td>
                    {cells.map((c, i) => (
                      <td key={i} className="num text-right">
                        {c ? (
                          <button type="button" className="hover:text-brand" onClick={() => inspect(c)} aria-label={`Inspect ${label} ${periods[i]}`}>
                            {fmtValue(c.value, c.scale, c.currency)}
                            {c.status === "DERIVED" ? <sup className="text-brand"> c</sup> : null}
                          </button>
                        ) : (
                          <span className="text-subtle" title="Not disclosed / not ingested">—</span>
                        )}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[12px] text-subtle">
            Reporting currency {obs[0]?.currency}, millions. <sup className="text-brand">c</sup> = deterministic calculation. — = not disclosed or not ingested (never zero).
            Figures come from curated filing summaries; click a value to inspect its source.
          </p>
        </div>
      )}
    </section>
  );
}

function EdgeTable({ title, rows, side }: { title: string; rows: EdgeRow[]; side: "src" | "dst" }) {
  const { open } = useEvidence();
  return (
    <div>
      <h3 className="mb-2 text-[13px] font-semibold">{title}</h3>
      {!rows.length ? (
        <p className="ws-empty">None documented.</p>
      ) : (
        <table className="ws-table">
          <thead><tr><th>Counterparty</th><th>Type</th><th className="text-right">Dep.</th><th>Evidence</th></tr></thead>
          <tbody>
            {rows.map((e) => {
              const sym = side === "src" ? e.src_symbol : e.dst_symbol;
              const iid = side === "src" ? e.src_instrument_id : e.dst_instrument_id;
              return (
                <tr key={e.edge_id} className="is-clickable" onClick={() => open({ tab: "edge", label: e.edge_id, edge: e, sourceId: e.source_id })}>
                  <td className="num">
                    {iid ? <Link className="ws-link" href={`/companies/${encodeURIComponent(iid)}`} onClick={(ev) => ev.stopPropagation()}>{sym}</Link> : sym}
                  </td>
                  <td className="text-[12px]">{e.relationship_type}</td>
                  <td className="num text-right">{e.revenue_dependency_pct != null ? `${e.revenue_dependency_pct}%` : "—"}</td>
                  <td><span className={`ws-tag ${e.evidence_status === "EXTRACTED" ? "ws-tag-pos" : "ws-tag-warn"}`}>{e.evidence_status}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CompanyBody({ iid }: { iid: string }) {
  const { data, error, isLoading } = useCompany(iid);
  const prices = usePrices(iid);
  const { open } = useEvidence();
  const latest = useMemo(() => {
    const m = new Map<string, Obs>();
    (data?.observations ?? []).forEach((o) => m.set(o.metric_code, o));
    return m;
  }, [data]);
  const inspect = useInspect(data?.instrument.symbol ?? "", iid);

  if (isLoading) return <p className="ws-empty">Loading company…</p>;
  if (error) {
    return (
      <p role="alert" className="ws-banner ws-banner-neg">
        {error instanceof ApiNotFoundError ? `Instrument ${iid} is not in the AYVID universe.` : "Could not load company."}{" "}
        <Link className="ws-link" href="/companies">Browse instruments</Link>
      </p>
    );
  }
  if (!data) return null;
  const inst = data.instrument;
  const rev = data.observations.filter((o) => o.metric_code === "REVENUE" && o.fiscal_period.startsWith("FY")).at(-1);
  const opm = latest.get("OPERATING_MARGIN_PCT");
  const priceSource = prices.data?.source_id;

  return (
    <div>
      <p className="label-mono">{inst.instrument_id} · {inst.exchange} · {inst.currency}</p>
      <h1 className="ws-h1 mt-2">{inst.name}</h1>
      <div className="mt-2 flex flex-wrap gap-2">
        <span className="ws-tag">{inst.industry ?? "Unclassified"}</span>
        <span className="ws-tag ws-tag-brand">{inst.data_tier.replace(/_/g, " ")}</span>
        {inst.cik ? <span className="ws-tag">CIK {inst.cik}</span> : null}
        <Link className="ws-tag hover:text-fg"
              href={`/research?q=${encodeURIComponent(`${inst.symbol} key risks, margins and invalidation conditions`)}&iid=${encodeURIComponent(iid)}&run=1`}>
          Research this →
        </Link>
      </div>
      {data.freshness.is_stale ? (
        <p className="ws-banner ws-banner-warn mt-4">
          STALE DATA (As of {data.freshness.as_of}) — the local price series ended {data.freshness.age_days} days ago. Do not use for live market-cap or valuation ratios.
        </p>
      ) : null}

      <section className="ws-section" aria-labelledby="ov">
        <h2 id="ov" className="ws-h2">Overview · price &amp; regime</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {MARKET_METRICS.map((code) => {
            const o = latest.get(code);
            return (
              <button key={code} type="button" disabled={!o} onClick={() => o && inspect(o, data.freshness.is_stale)} className="ws-card text-left !p-3 disabled:opacity-60">
                <span className="block text-[11px] text-subtle">{o?.metric_label ?? code}</span>
                <span className="num block text-[17px]">{o ? fmtValue(o.value, o.scale, o.currency) : "UNKNOWN"}</span>
              </button>
            );
          })}
        </div>
        <div className="mt-4 h-[220px]" aria-label="Daily close price chart">
          {prices.data ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={prices.data.bars} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <XAxis dataKey="t" tick={{ fontSize: 10, fill: "hsl(var(--fg-subtle))" }} minTickGap={48} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "hsl(var(--fg-subtle))" }} width={48} />
                <Tooltip contentStyle={{ background: "hsl(var(--panel))", border: "1px solid hsl(var(--border))", fontSize: 12 }} />
                <Line type="monotone" dataKey="close" stroke="hsl(var(--brand))" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : prices.error ? (
            <p className="ws-empty">No local price series.</p>
          ) : (
            <p className="ws-empty">Loading prices…</p>
          )}
        </div>
        {prices.data && priceSource ? (
          <button type="button" className="mt-1 text-[11px] text-subtle hover:text-fg" onClick={() => open({ tab: "source", label: "Price series", sourceId: priceSource })}>
            Source: local OHLCV · {prices.data.bars.length} daily bars · as of {prices.data.freshness.as_of} ↗
          </button>
        ) : null}
      </section>

      <Financials data={data} />

      <section className="ws-section" aria-labelledby="exp">
        <h2 id="exp" className="ws-h2">Supply-chain &amp; second-order exposure</h2>
        <div className="ws-split">
          <EdgeTable title="Upstream (depends on)" rows={data.exposures.upstream} side="src" />
          <EdgeTable title="Downstream (exposed to this company)" rows={data.exposures.downstream} side="dst" />
        </div>
      </section>

      <section className="ws-section" aria-labelledby="val">
        <h2 id="val" className="ws-h2">Valuation &amp; sensitivity sandbox</h2>
        <ValuationSandbox baseRevenue={rev ? Number(rev.value) : null} baseLabel={rev?.fiscal_period} currency={rev?.currency}
                          observedMargin={opm ? Number(opm.value) : null} />
      </section>

      <section className="ws-section" aria-labelledby="fil">
        <h2 id="fil" className="ws-h2">Earnings &amp; filings</h2>
        <ul className="space-y-1.5">
          {data.sources.filter((s) => s.doc_type !== "PRICE_SERIES").map((s) => (
            <li key={s.source_id}>
              <button type="button" className="ws-nav w-full text-left" onClick={() => open({ tab: "source", label: s.doc_type, source: s })}>
                <span className="ws-tag shrink-0">{s.doc_type}</span>
                <span className="truncate">{s.title}</span>
                <span className="num ml-auto shrink-0 text-[11px] text-subtle">{s.published_at}</span>
              </button>
            </li>
          ))}
          {!data.sources.some((s) => s.doc_type !== "PRICE_SERIES") ? <li className="ws-empty">No filings ingested for this instrument.</li> : null}
        </ul>
      </section>

      <section className="ws-section" aria-labelledby="th">
        <h2 id="th" className="ws-h2">Linked theses</h2>
        {data.theses.length ? (
          <ul>
            {data.theses.map((t) => (
              <li key={t.thesis_id}><Link className="ws-nav" href={`/theses/${t.thesis_id}`}><HealthDot health={t.health} /> {t.title}</Link></li>
            ))}
          </ul>
        ) : (
          <p className="ws-empty">No theses reference this instrument yet.</p>
        )}
      </section>
    </div>
  );
}

export default function CompanyPage() {
  const { instrumentId } = useParams<{ instrumentId: string }>();
  return (
    <Suspense fallback={null}>
      <CompanyBody iid={decodeURIComponent(instrumentId)} />
    </Suspense>
  );
}
