"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { fmtValue, researchApi, type ScreenFilter, type ScreenResult } from "@/lib/research";

const SAMPLE =
  "Semiconductor and software companies with positive 90-day momentum, annualized volatility below 50%, and documented supply-chain links to TSM";
const FIELDS = ["industry", "country", "exchange", "RETURN_90D_PCT", "REALIZED_VOL_ANN", "MAX_DRAWDOWN_PCT", "HURST_EXPONENT", "GROSS_MARGIN_PCT", "REVENUE_GROWTH_PCT", "supply_chain"];
const OPS = [">", "<", ">=", "<=", "==", "in", "linked_to"];

function valueToText(v: ScreenFilter["value"]): string {
  return Array.isArray(v) ? v.join(", ") : String(v);
}
function textToValue(field: string, op: string, text: string): ScreenFilter["value"] {
  if (op === "in") return text.split(",").map((s) => s.trim()).filter(Boolean);
  if (op === "linked_to" || op === "==" || ["industry", "country", "exchange"].includes(field)) return text.trim();
  return Number(text);
}

function ScreenerBody() {
  const params = useSearchParams();
  const router = useRouter();
  const [query, setQuery] = useState(params.get("q") ?? SAMPLE);
  const [filters, setFilters] = useState<ScreenFilter[] | null>(null);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  const execute = useCallback(async (q: string, f: ScreenFilter[] | null) => {
    setBusy(true);
    setError(null);
    try {
      const r = await researchApi.screen(q, f ?? undefined);
      setResult(r);
      setFilters(r.plan.filters);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Screen failed");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    void execute(query, null);
  }, [execute, query]);

  const translate = () => {
    router.replace(`/screener?q=${encodeURIComponent(query)}`, { scroll: false });
    void execute(query, null);
  };
  const update = (i: number, patch: Partial<ScreenFilter>) =>
    setFilters((fs) => (fs ?? []).map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const metricCols = result ? Object.keys(result.matches[0]?.metrics ?? {}) : [];

  return (
    <div>
      <p className="label-mono">Screener</p>
      <h1 className="ws-h1 mt-2">Natural language in, typed filter plan out</h1>
      <p className="mt-2 text-[14px] text-muted">The plan is parsed by rules, shown to you, editable, and executed deterministically — no model in the loop.</p>
      <form className="mt-4" onSubmit={(e) => { e.preventDefault(); translate(); }}>
        <label htmlFor="sq" className="sr-only">Screen description</label>
        <textarea id="sq" className="ws-query" rows={2} value={query} maxLength={1000} onChange={(e) => setQuery(e.target.value)} />
        <div className="mt-2 flex justify-end">
          <button type="submit" className="ws-btn-primary" disabled={busy}>{busy ? "Screening…" : "Translate & run"}</button>
        </div>
      </form>

      {filters ? (
        <section className="ws-section" aria-labelledby="plan">
          <h2 id="plan" className="ws-h2">Filter plan (editable)</h2>
          {!filters.length ? (
            <p className="ws-empty">No filters recognised{result?.plan.unparsed.length ? ` in “${result.plan.unparsed[0]}”` : ""}. Add one below.</p>
          ) : null}
          <ul className="space-y-2">
            {filters.map((f, i) => (
              <li key={i} className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[1.2fr_0.7fr_1.6fr_auto]">
                <select aria-label="Field" className="ws-input" value={f.field} onChange={(e) => update(i, { field: e.target.value })}>
                  {FIELDS.map((x) => <option key={x}>{x}</option>)}
                </select>
                <select aria-label="Operator" className="ws-input" value={f.op} onChange={(e) => update(i, { op: e.target.value })}>
                  {OPS.map((x) => <option key={x}>{x}</option>)}
                </select>
                <input aria-label="Value" className="ws-input num" defaultValue={valueToText(f.value)} key={`${i}-${valueToText(f.value)}`}
                       onBlur={(e) => update(i, { value: textToValue(f.field, f.op, e.target.value) })} />
                <button type="button" className="ws-btn" aria-label={`Remove filter ${f.field}`} onClick={() => setFilters(filters.filter((_, j) => j !== i))}>×</button>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2">
            <button type="button" className="ws-btn" onClick={() => setFilters([...filters, { field: "RETURN_90D_PCT", op: ">", value: 0 }])}>Add filter</button>
            <button type="button" className="ws-btn-primary" disabled={busy} onClick={() => void execute(query, filters)}>Run edited plan</button>
          </div>
        </section>
      ) : null}

      {error ? <p role="alert" className="ws-banner ws-banner-neg mt-4">{error}</p> : null}
      {result ? (
        <section className="ws-section" aria-labelledby="res" aria-live="polite">
          <h2 id="res" className="ws-h2">{result.matches.length} matches</h2>
          <p className="ws-banner ws-banner-info text-[12px]">
            Universe: {result.universe.size} instruments ({result.universe.description}) · bars {result.universe.date_range.first_bar} →{" "}
            {result.universe.date_range.last_bar} · {result.universe.stale_instruments} stale. {result.missing_data.rule} Excluded for missing data:{" "}
            {result.missing_data.excluded_count}.
          </p>
          {result.matches.length ? (
            <div className="ws-scroll-x mt-3">
              <table className="ws-table">
                <thead>
                  <tr><th>Instrument</th><th>Industry</th>{metricCols.map((c) => <th key={c} className="text-right">{c.replace(/_/g, " ")}</th>)}<th>As of</th></tr>
                </thead>
                <tbody>
                  {result.matches.map((m) => (
                    <tr key={m.instrument_id}>
                      <td className="num"><Link className="ws-link" href={`/companies/${encodeURIComponent(m.instrument_id)}`}>{m.instrument_id}</Link></td>
                      <td className="text-muted">{m.industry}</td>
                      {metricCols.map((c) => <td key={c} className="num text-right">{fmtValue(m.metrics[c], c === "CLOSE_PRICE" ? "UNITS" : "PERCENT")}</td>)}
                      <td>{m.is_stale ? <span className="ws-tag ws-tag-warn">Stale {m.last_bar}</span> : <span className="num">{m.last_bar}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ws-empty">Nothing in the universe passes every filter.</p>
          )}
        </section>
      ) : null}
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <Suspense fallback={<p className="ws-empty">Loading…</p>}>
      <ScreenerBody />
    </Suspense>
  );
}
