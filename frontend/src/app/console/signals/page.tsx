"use client";
import { LineChart } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { useSignals } from "@/lib/hooks";
import { fmtDateTime, fmtMoney, fmtNumber } from "@/lib/format";
import type { Signal } from "@/lib/api";

export default function SignalsPage() {
  const { data, error, isLoading } = useSignals();
  const signals: Signal[] = data?.signals ?? [];

  return (
    <div className="flex flex-col gap-6 fade-up">
      <header>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">Signals</h1>
        <p className="mt-1 text-[13px] text-muted">
          Live signal book — every signal the orchestrator has emitted, with
          conviction, expected R-multiple, and TTL.
        </p>
      </header>

      <Card
        title={`Signal book · ${data?.count ?? 0} entries`}
        subtitle="Updates every 15 seconds"
      >
        {isLoading && <div className="skeleton h-32" />}
        {error && (
          <Empty
            icon={<LineChart className="h-8 w-8" />}
            title="Failed to load signal book"
            hint={String(error)}
          />
        )}
        {!isLoading && !error && signals.length === 0 && (
          <Empty
            icon={<LineChart className="h-8 w-8" />}
            title="Signal book is empty"
            hint="Run `ayvid trade signals` from the CLI to populate, or wait for the next pipeline cycle."
          />
        )}
        {signals.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="py-2 pr-4 font-medium">Ticker</th>
                  <th className="py-2 pr-4 font-medium">Side</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 text-right font-medium">Conviction</th>
                  <th className="py-2 pr-4 text-right font-medium">Exp R</th>
                  <th className="py-2 pr-4 text-right font-medium">Stop / TP</th>
                  <th className="py-2 pr-4 text-right font-medium">Entry</th>
                  <th className="py-2 pr-4 font-medium">Strategy</th>
                  <th className="py-2 font-medium">Computed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[hsl(var(--border))]">
                {signals.map((s, i) => {
                  const long = s.direction === "LONG";
                  const conv = (s.conviction ?? 0) * 100;
                  return (
                    <tr key={`${s.ticker}-${i}`} className="hover:bg-[hsl(var(--bg-sunken))]">
                      <td className="num py-2 pr-4 font-semibold text-fg">{s.ticker}</td>
                      <td className="py-2 pr-4">
                        <Badge variant={long ? "pos" : "neg"}>{s.direction}</Badge>
                      </td>
                      <td className="py-2 pr-4 text-[11px] uppercase tracking-wider text-muted">
                        {s.instrument_type}
                      </td>
                      <td className="num py-2 pr-4 text-right text-fg">{fmtNumber(conv, 0)}%</td>
                      <td className="num py-2 pr-4 text-right text-fg">{s.expected_r ?? "—"}</td>
                      <td className="num py-2 pr-4 text-right text-muted">
                        {s.stop_loss_pct ?? "—"}% / {s.take_profit_pct ?? "—"}%
                      </td>
                      <td className="num py-2 pr-4 text-right text-muted">
                        {fmtMoney(s.entry_price_at_compute)}
                      </td>
                      <td className="py-2 pr-4 text-[12px] text-muted">{s.strategy_name}</td>
                      <td className="num py-2 text-[12px] text-muted">
                        {fmtDateTime(s.computed_at)}
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
