"use client";
import { use } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, BarChart3 } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { fmtNumber, fmtPct } from "@/lib/format";
import { getJSON } from "@/lib/api";

const fetcher = <T,>(path: string) => getJSON<T>(path);

interface WindowResult {
  window_id?: string;
  start_date?: string;
  end_date?: string;
  total_return_pct?: number;
  sharpe_ratio?: number;
  max_drawdown_pct?: number;
  total_trades?: number;
  num_trades?: number;
  cost_drag_pct?: number;
  status?: string;
}

interface ScenarioResult {
  scenario_id?: string;
  scenario_name?: string;
  start_date?: string;
  end_date?: string;
  total_return_pct?: number;
  sharpe_ratio?: number;
  max_drawdown_pct?: number;
  num_trades?: number;
  cost_drag_pct?: number;
  status?: string;
}

interface BacktestDetail {
  start_date?: string;
  end_date?: string;
  span_start?: string;
  span_end?: string;
  initial_equity?: number;
  generated_at?: string;
  windows?: WindowResult[];
  scenarios?: ScenarioResult[];
  pooled_total_trades?: number;
  aggregate_compounded_return_pct?: number;
  aggregate_cagr_pct?: number;
  median_sharpe?: number;
  pct_positive_windows?: number;
  pct_positive_scenarios?: number;
  total_return_pct?: number;
  sharpe_ratio?: number;
  max_drawdown_pct?: number;
}

export default function BacktestDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const { data, error, isLoading } = useSWR<BacktestDetail>(
    `/live/backtests/${encodeURIComponent(name)}`,
    fetcher,
  );

  return (
    <div className="flex flex-col gap-6 fade-up">
      <Link
        href="/console/backtests"
        className="inline-flex items-center gap-1.5 text-[12px] text-muted hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> All backtests
      </Link>

      <header>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">{name}</h1>
        <p className="mt-1 text-[13px] text-muted">
          Generated {data?.generated_at ?? "—"}
        </p>
      </header>

      {isLoading && <div className="skeleton h-32" />}
      {error && (
        <Card>
          <Empty
            icon={<BarChart3 className="h-8 w-8" />}
            title="Failed to load report"
            hint={String(error)}
          />
        </Card>
      )}
      {data && (
        <>
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <KV label="Compounded" value={fmtPct(data.aggregate_compounded_return_pct ?? data.total_return_pct ?? 0)} accent={(data.aggregate_compounded_return_pct ?? data.total_return_pct ?? 0) >= 0 ? "pos" : "neg"} />
            <KV label="CAGR" value={fmtPct(data.aggregate_cagr_pct ?? 0)} />
            <KV label="Median Sharpe" value={data.median_sharpe?.toFixed(2) ?? data.sharpe_ratio?.toFixed(2) ?? "—"} />
            <KV label="Pooled trades" value={fmtNumber(data.pooled_total_trades ?? null)} />
          </section>

          {data.scenarios && data.scenarios.length > 0 && (
            <Card title={`Per-scenario · ${data.scenarios.length}`}>
              <ScenarioTable scenarios={data.scenarios} />
            </Card>
          )}
          {data.windows && data.windows.length > 0 && (
            <Card title={`Per-window · ${data.windows.length}`}>
              <WindowsTable windows={data.windows} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function KV({ label, value, accent }: { label: string; value: React.ReactNode; accent?: "pos" | "neg" }) {
  const cls = accent === "pos" ? "text-pos" : accent === "neg" ? "text-neg" : "text-fg";
  return (
    <div className="card p-5">
      <div className="text-[11px] uppercase tracking-[0.16em] text-muted">{label}</div>
      <div className={`num mt-2 text-3xl font-semibold tracking-tight ${cls}`}>{value}</div>
    </div>
  );
}

function ScenarioTable({ scenarios }: { scenarios: ScenarioResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
            <th className="py-2 pr-4 font-medium">Scenario</th>
            <th className="py-2 pr-4 font-medium">Span</th>
            <th className="py-2 pr-4 text-right font-medium">Return</th>
            <th className="py-2 pr-4 text-right font-medium">Sharpe</th>
            <th className="py-2 pr-4 text-right font-medium">Max DD</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 pr-4 text-right font-medium">Cost drag</th>
            <th className="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[hsl(var(--border))]">
          {scenarios.map((r, i) => {
            const ret = r.total_return_pct ?? 0;
            return (
              <tr key={`${r.scenario_id ?? i}`} className="hover:bg-[hsl(var(--bg-sunken))]">
                <td className="py-2 pr-4 font-medium text-fg">{r.scenario_name ?? r.scenario_id ?? "—"}</td>
                <td className="num py-2 pr-4 text-muted">{r.start_date} → {r.end_date}</td>
                <td className={`num py-2 pr-4 text-right font-semibold ${ret >= 0 ? "text-pos" : "text-neg"}`}>{fmtPct(ret)}</td>
                <td className="num py-2 pr-4 text-right text-fg">{r.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                <td className="num py-2 pr-4 text-right text-neg">{fmtPct(r.max_drawdown_pct ?? 0)}</td>
                <td className="num py-2 pr-4 text-right text-fg">{fmtNumber(r.num_trades ?? null)}</td>
                <td className="num py-2 pr-4 text-right text-muted">{fmtPct(r.cost_drag_pct ?? 0)}</td>
                <td className="py-2"><Badge variant={r.status === "ok" ? "pos" : "info"}>{r.status ?? "—"}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function WindowsTable({ windows }: { windows: WindowResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
            <th className="py-2 pr-4 font-medium">Window</th>
            <th className="py-2 pr-4 font-medium">Span</th>
            <th className="py-2 pr-4 text-right font-medium">Return</th>
            <th className="py-2 pr-4 text-right font-medium">Sharpe</th>
            <th className="py-2 pr-4 text-right font-medium">Max DD</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 pr-4 text-right font-medium">Cost drag</th>
            <th className="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[hsl(var(--border))]">
          {windows.map((w, i) => {
            const ret = w.total_return_pct ?? 0;
            return (
              <tr key={`${w.window_id ?? i}`} className="hover:bg-[hsl(var(--bg-sunken))]">
                <td className="num py-2 pr-4 text-fg">{w.window_id ?? `#${i + 1}`}</td>
                <td className="num py-2 pr-4 text-muted">{w.start_date} → {w.end_date}</td>
                <td className={`num py-2 pr-4 text-right font-semibold ${ret >= 0 ? "text-pos" : "text-neg"}`}>{fmtPct(ret)}</td>
                <td className="num py-2 pr-4 text-right text-fg">{w.sharpe_ratio?.toFixed(2) ?? "—"}</td>
                <td className="num py-2 pr-4 text-right text-neg">{fmtPct(w.max_drawdown_pct ?? 0)}</td>
                <td className="num py-2 pr-4 text-right text-fg">{fmtNumber(w.total_trades ?? w.num_trades ?? null)}</td>
                <td className="num py-2 pr-4 text-right text-muted">{fmtPct(w.cost_drag_pct ?? 0)}</td>
                <td className="py-2"><Badge variant={w.status === "ok" ? "pos" : "info"}>{w.status ?? "—"}</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
