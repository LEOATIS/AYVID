"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useCompanies } from "@/lib/research";

export default function CompaniesPage() {
  const { data, error, isLoading } = useCompanies();
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data ?? []).filter((i) =>
      !needle || [i.symbol, i.name, i.instrument_id, i.industry ?? ""].some((v) => v.toLowerCase().includes(needle)),
    );
  }, [data, q]);

  return (
    <div className="ws-main-wide">
      <p className="label-mono">Companies</p>
      <h1 className="ws-h1 mt-2">Instrument universe</h1>
      <p className="mt-2 text-[14px] text-muted">
        Every instrument with a local price series. Data tier says what AYVID can verify for it.
      </p>
      <label htmlFor="co-filter" className="sr-only">Filter companies</label>
      <input id="co-filter" name="filter" autoComplete="off" className="ws-input mt-4 max-w-sm" placeholder="Filter by ticker, name or industry…"
             value={q} onChange={(e) => setQ(e.target.value)} />
      {isLoading ? <p className="ws-empty mt-4">Loading…</p> : null}
      {error ? <p role="alert" className="ws-banner ws-banner-neg mt-4">Could not load instruments.</p> : null}
      {data ? (
        <div className="ws-scroll-x mt-4">
          <table className="ws-table">
            <thead>
              <tr><th>Instrument ID</th><th>Name</th><th>Industry</th><th>Currency</th><th>Data tier</th><th>Last bar</th></tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.instrument_id}>
                  <td className="num"><Link className="ws-link" href={`/companies/${encodeURIComponent(i.instrument_id)}`}>{i.instrument_id}</Link></td>
                  <td>{i.name}</td>
                  <td className="text-muted">{i.industry ?? "—"}</td>
                  <td className="num">{i.currency}</td>
                  <td><span className="ws-tag">{i.data_tier.replace(/_/g, " ")}</span></td>
                  <td className="num text-muted">{i.last_bar}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[12px] text-subtle">{rows.length} of {data.length} instruments</p>
        </div>
      ) : null}
    </div>
  );
}
