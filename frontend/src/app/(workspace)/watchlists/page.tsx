"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { getJSON } from "@/lib/api";
import { fmtValue, researchApi, useCompanies, useWatchlists, type Watchlist } from "@/lib/research";
import { HealthDot } from "@/components/workspace/WorkspaceShell";

interface Notification {
  notification_id: string;
  created_at: string;
  kind: string;
  title: string;
  body: string;
  ref_type: string;
  ref_id: string;
}

const DEFAULT_LIST = ["US:NASDAQ:NVDA", "US:NYSE:TSM", "US:NASDAQ:ASML", "US:NASDAQ:AAPL", "US:NASDAQ:AMD", "IN:NSE:INFY"];

function WatchlistCard({ wl }: { wl: Watchlist }) {
  const { mutate } = useSWRConfig();
  const companies = useCompanies();
  const [add, setAdd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      await mutate("/watchlists");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Request failed");
    }
  };
  return (
    <section className="ws-section" aria-label={wl.name}>
      <h2 className="ws-h2">{wl.name}</h2>
      <div className="ws-scroll-x">
        <table className="ws-table">
          <thead>
            <tr><th>Instrument</th><th className="text-right">Close</th><th className="text-right">90D return</th><th className="text-right">90D vol</th><th>Freshness</th><th>Theses</th><th /></tr>
          </thead>
          <tbody>
            {wl.items.map((i) => (
              <tr key={i.instrument_id}>
                <td className="num"><Link className="ws-link" href={`/companies/${encodeURIComponent(i.instrument_id)}`}>{i.instrument_id}</Link></td>
                <td className="num text-right">{fmtValue(i.metrics.CLOSE_PRICE, "UNITS")}</td>
                <td className="num text-right">{fmtValue(i.metrics.RETURN_90D_PCT, "PERCENT")}</td>
                <td className="num text-right">{fmtValue(i.metrics.REALIZED_VOL_ANN, "PERCENT")}</td>
                <td>{i.freshness.is_stale ? <span className="ws-tag ws-tag-warn">Stale {i.freshness.as_of}</span> : <span className="ws-tag ws-tag-pos">Fresh</span>}</td>
                <td className="text-[12px]">
                  {i.theses.length ? (
                    i.theses.map((t) => (
                      <Link key={t.thesis_id} href={`/theses/${t.thesis_id}`} className="mr-2 inline-flex items-center gap-1 hover:text-brand">
                        <HealthDot health={t.health} />
                        {t.title.slice(0, 28)}
                      </Link>
                    ))
                  ) : (
                    <span className="text-subtle">—</span>
                  )}
                </td>
                <td>
                  <button type="button" className="ws-btn !px-2 !py-0.5 text-[12px]" aria-label={`Remove ${i.instrument_id}`}
                          onClick={() => void act(() => researchApi.removeWatchItem(wl.watchlist_id, i.instrument_id))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex gap-2">
        <label className="sr-only" htmlFor={`add-${wl.watchlist_id}`}>Add instrument</label>
        <select id={`add-${wl.watchlist_id}`} className="ws-input w-auto" value={add} onChange={(e) => setAdd(e.target.value)}>
          <option value="">Add instrument…</option>
          {(companies.data ?? []).map((c) => <option key={c.instrument_id} value={c.instrument_id}>{c.instrument_id}</option>)}
        </select>
        <button type="button" className="ws-btn" disabled={!add} onClick={() => void act(() => researchApi.addWatchItem(wl.watchlist_id, add)).then(() => setAdd(""))}>
          Add
        </button>
      </div>
      {err ? <p role="alert" className="mt-2 text-[13px] text-neg">{err}</p> : null}
    </section>
  );
}

export default function WatchlistsPage() {
  const { data, error, isLoading } = useWatchlists();
  const notifications = useSWR<Notification[]>("/notifications", (p: string) => getJSON<Notification[]>(p), { revalidateOnFocus: false });
  const { mutate } = useSWRConfig();
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);

  const runChecks = async () => {
    setChecking(true);
    try {
      const r = await researchApi.runChecks();
      setCheckResult(`Evaluated ${r.evaluated} theses.`);
      await Promise.all([mutate("/watchlists"), mutate("/theses"), notifications.mutate()]);
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : "Check failed");
    } finally {
      setChecking(false);
    }
  };
  const createDefault = async () => {
    try {
      await researchApi.createWatchlist("Semis & AI infrastructure", DEFAULT_LIST);
      await mutate("/watchlists");
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : "Could not create watchlist");
    }
  };

  return (
    <div>
      <p className="label-mono">Watchlists</p>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="ws-h1 mt-2">Watchlists &amp; change monitoring</h1>
        <button type="button" className="ws-btn-primary" onClick={() => void runChecks()} disabled={checking}>
          {checking ? "Evaluating…" : "Run check now"}
        </button>
      </div>
      <p className="mt-2 text-[14px] text-muted">
        Monitoring is opt-in and manual: “Run check now” evaluates every thesis invalidation condition and logs what changed.
      </p>
      {checkResult ? <p className="ws-banner ws-banner-info mt-3" aria-live="polite">{checkResult}</p> : null}
      {isLoading ? <p className="ws-empty mt-4">Loading…</p> : null}
      {error ? <p role="alert" className="ws-banner ws-banner-neg mt-4">Could not load watchlists.</p> : null}
      {data && !data.length ? (
        <div className="ws-card mt-5">
          <p className="text-[14px]">No watchlists yet.</p>
          <button type="button" className="ws-btn mt-3" onClick={() => void createDefault()}>Create “Semis &amp; AI infrastructure”</button>
        </div>
      ) : null}
      {(data ?? []).map((wl) => <WatchlistCard key={wl.watchlist_id} wl={wl} />)}

      <section className="ws-section" aria-labelledby="notif">
        <h2 id="notif" className="ws-h2">Notifications</h2>
        {notifications.data?.length ? (
          <ul className="space-y-1.5 text-[13px]">
            {notifications.data.map((n) => (
              <li key={n.notification_id} className="grid grid-cols-[130px_1fr] gap-3">
                <span className="num text-[11px] text-subtle">{n.created_at.replace("T", " ").slice(0, 16)}</span>
                <span>
                  <span className={`ws-tag mr-2 ${n.kind === "CONDITION_TRIGGERED" ? "ws-tag-neg" : ""}`}>{n.kind.replace(/_/g, " ")}</span>
                  {n.ref_type === "THESIS" ? <Link className="ws-link" href={`/theses/${n.ref_id}`}>{n.title}</Link> : n.title}
                  <span className="block text-[12px] text-muted">{n.body}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ws-empty">No notifications. Triggered conditions and recorded events appear here.</p>
        )}
      </section>
    </div>
  );
}
