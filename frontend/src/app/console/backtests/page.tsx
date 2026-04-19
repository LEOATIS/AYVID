"use client";
import Link from "next/link";
import { ArrowUpRight, BarChart3 } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { useBacktests } from "@/lib/hooks";
import { fmtNumber, fmtPct } from "@/lib/format";
import type { BacktestSummary } from "@/lib/api";

const KIND_VARIANT: Record<BacktestSummary["kind"], "pos" | "neg" | "info" | "neutral"> = {
  shock_driven: "pos",
  walkforward: "info",
  single_window: "neutral",
  unknown: "neutral",
};

export default function BacktestsPage() {
  const { data, isLoading, error } = useBacktests();
  const reports: BacktestSummary[] = data?.reports ?? [];

  return (
    <div className="flex flex-col gap-6 fade-up">
      <header>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">Backtests</h1>
        <p className="mt-1 text-[13px] text-muted">
          Every report committed under <span className="num text-fg">document/backtests/</span>.
          Click a row for full per-window or per-scenario detail.
        </p>
      </header>

      <Card title={`Reports · ${reports.length}`}>
        {isLoading && <div className="skeleton h-32" />}
        {error && (
          <Empty
            icon={<BarChart3 className="h-8 w-8" />}
            title="Failed to load reports"
            hint={String(error)}
          />
        )}
        {!isLoading && !error && reports.length === 0 && (
          <Empty icon={<BarChart3 className="h-8 w-8" />} title="No reports yet" />
        )}
        {reports.length > 0 && (
          <div className="-mx-4 overflow-x-auto sm:mx-0">
            <table className="min-w-full whitespace-nowrap text-[13px]">
              <thead>
                <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">Kind</th>
                  <th className="px-4 py-2 font-medium">Span</th>
                  <th className="px-4 py-2 text-right font-medium">Compounded</th>
                  <th className="px-4 py-2 text-right font-medium">CAGR</th>
                  <th className="px-4 py-2 text-right font-medium">Med. Sharpe</th>
                  <th className="px-4 py-2 text-right font-medium">Trades</th>
                  <th className="px-4 py-2 text-right font-medium">% positive</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[hsl(var(--border))]">
                {reports.map((r) => {
                  const ret = r.compounded_return_pct ?? r.total_return_pct;
                  const cls = (ret ?? 0) >= 0 ? "text-pos" : "text-neg";
                  return (
                    <tr key={r.name} className="hover:bg-[hsl(var(--bg-sunken))]">
                      <td className="num px-4 py-2.5 font-medium text-fg">{r.name}</td>
                      <td className="px-4 py-2.5">
                        <Badge variant={KIND_VARIANT[r.kind]}>{r.kind.replace("_", "-")}</Badge>
                      </td>
                      <td className="num px-4 py-2.5 text-muted">{r.span ?? "—"}</td>
                      <td className={`num px-4 py-2.5 text-right font-semibold ${cls}`}>
                        {fmtPct(ret ?? 0)}
                      </td>
                      <td className="num px-4 py-2.5 text-right text-fg">{fmtPct(r.cagr_pct ?? 0)}</td>
                      <td className="num px-4 py-2.5 text-right text-fg">{r.median_sharpe?.toFixed(2) ?? "—"}</td>
                      <td className="num px-4 py-2.5 text-right text-fg">{fmtNumber(r.pooled_trades ?? r.total_trades ?? null)}</td>
                      <td className="num px-4 py-2.5 text-right text-fg">{r.pct_positive != null ? `${r.pct_positive.toFixed(0)}%` : "—"}</td>
                      <td className="px-4 py-2.5">
                        <Link
                          href={`/console/backtests/${r.name}`}
                          className="inline-flex items-center gap-1 text-[12px] text-brand hover:underline"
                        >
                          Open <ArrowUpRight className="h-3 w-3" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
