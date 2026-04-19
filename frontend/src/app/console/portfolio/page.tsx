"use client";
import { Briefcase, CircleDollarSign, Wallet } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Empty } from "@/components/ui/Empty";
import { Badge } from "@/components/ui/Badge";
import { useEquity, usePositions, useRunner, useTrades } from "@/lib/hooks";
import { fmtMoney, fmtNumber, fmtPct, fmtTime, fmtDate } from "@/lib/format";

interface PositionRow {
  ticker?: string;
  symbol?: string;
  quantity?: number;
  qty?: number;
  avg_price?: number;
  market_price?: number;
  market_value?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  side?: string;
}

interface TradeRow {
  timestamp?: string;
  ticker?: string;
  symbol?: string;
  side?: string;
  action?: string;
  quantity?: number;
  qty?: number;
  price?: number;
  notional?: number;
  status?: string;
}

export default function PortfolioPage() {
  const { data: equity } = useEquity(500);
  const { data: positions } = usePositions();
  const { data: trades } = useTrades(100);
  const { data: runner } = useRunner();

  const summary = equity?.summary;
  const initial = summary?.initial_equity ?? 0;
  const final = summary?.final_equity ?? initial;
  const positionRows: PositionRow[] = positions?.positions ?? [];
  const tradeRows: TradeRow[] = trades?.trades ?? [];

  return (
    <div className="flex flex-col gap-7 fade-up">
      <header>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">Portfolio</h1>
        <p className="mt-1 text-[13px] text-muted">
          Open positions, realized & unrealized P&amp;L, and recent fills from the
          live IB paper account.
        </p>
      </header>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          label="Equity"
          value={fmtMoney(final, { decimals: 0 })}
          delta={fmtPct(summary?.total_return_pct ?? 0)}
          trend={(summary?.total_return_pct ?? 0) >= 0 ? "up" : "down"}
          hint={`vs ${fmtMoney(initial, { decimals: 0 })} start`}
          icon={<Wallet className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Cash"
          value={fmtMoney(equity?.snapshots?.[equity.snapshots.length - 1]?.cash, { decimals: 0 })}
          icon={<CircleDollarSign className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Position value"
          value={fmtMoney(equity?.snapshots?.[equity.snapshots.length - 1]?.position_value, { decimals: 0 })}
          icon={<Briefcase className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Open positions"
          value={fmtNumber(summary?.open_positions ?? 0)}
          hint={`mode: ${runner?.mode ?? "—"}`}
        />
      </section>

      <Card title="Open positions" subtitle={positions?.as_of ? `as of ${fmtTime(positions.as_of)}` : undefined}>
        {positionRows.length === 0 ? (
          <Empty
            icon={<Briefcase className="h-8 w-8" />}
            title="No open positions"
            hint="The runner has not opened any positions yet, or this snapshot is empty."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="py-2 pr-4 font-medium">Symbol</th>
                  <th className="py-2 pr-4 font-medium">Side</th>
                  <th className="py-2 pr-4 text-right font-medium">Qty</th>
                  <th className="py-2 pr-4 text-right font-medium">Avg</th>
                  <th className="py-2 pr-4 text-right font-medium">Mark</th>
                  <th className="py-2 pr-4 text-right font-medium">Market value</th>
                  <th className="py-2 text-right font-medium">Unrealized P&amp;L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[hsl(var(--border))]">
                {positionRows.map((p, i) => {
                  const sym = p.ticker ?? p.symbol ?? "—";
                  const qty = p.quantity ?? p.qty ?? 0;
                  const upnl = p.unrealized_pnl ?? 0;
                  const sideLong = (p.side ?? (qty >= 0 ? "LONG" : "SHORT")).toUpperCase();
                  return (
                    <tr key={`${sym}-${i}`} className="hover:bg-[hsl(var(--bg-sunken))]">
                      <td className="num py-2 pr-4 font-medium text-fg">{sym}</td>
                      <td className="py-2 pr-4">
                        <Badge variant={sideLong === "LONG" ? "pos" : "neg"}>{sideLong}</Badge>
                      </td>
                      <td className="num py-2 pr-4 text-right text-fg">{fmtNumber(qty)}</td>
                      <td className="num py-2 pr-4 text-right text-muted">{fmtMoney(p.avg_price)}</td>
                      <td className="num py-2 pr-4 text-right text-muted">{fmtMoney(p.market_price)}</td>
                      <td className="num py-2 pr-4 text-right text-fg">{fmtMoney(p.market_value)}</td>
                      <td className={`num py-2 text-right ${upnl >= 0 ? "text-pos" : "text-neg"}`}>
                        {fmtMoney(upnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent fills" subtitle="Latest trades from the live runner">
        {tradeRows.length === 0 ? (
          <Empty
            icon={<Briefcase className="h-8 w-8" />}
            title="No fills yet"
            hint="The runner has not executed any trades. Fills appear here when AUTO_EXECUTE is on or you trigger /api/v1/trade/execute."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-app text-left text-[11px] uppercase tracking-wider text-muted">
                  <th className="py-2 pr-4 font-medium">Time</th>
                  <th className="py-2 pr-4 font-medium">Symbol</th>
                  <th className="py-2 pr-4 font-medium">Side</th>
                  <th className="py-2 pr-4 text-right font-medium">Qty</th>
                  <th className="py-2 pr-4 text-right font-medium">Price</th>
                  <th className="py-2 pr-4 text-right font-medium">Notional</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[hsl(var(--border))]">
                {tradeRows.map((t, i) => {
                  const sym = t.ticker ?? t.symbol ?? "—";
                  const side = (t.side ?? t.action ?? "—").toUpperCase();
                  const qty = t.quantity ?? t.qty ?? 0;
                  return (
                    <tr key={`${sym}-${i}`} className="hover:bg-[hsl(var(--bg-sunken))]">
                      <td className="num py-2 pr-4 text-muted">{fmtTime(t.timestamp)}</td>
                      <td className="num py-2 pr-4 font-medium text-fg">{sym}</td>
                      <td className="py-2 pr-4">
                        <Badge variant={side.includes("BUY") || side === "LONG" ? "pos" : "neg"}>{side}</Badge>
                      </td>
                      <td className="num py-2 pr-4 text-right">{fmtNumber(qty)}</td>
                      <td className="num py-2 pr-4 text-right">{fmtMoney(t.price)}</td>
                      <td className="num py-2 pr-4 text-right">{fmtMoney(t.notional)}</td>
                      <td className="py-2">
                        <Badge variant={t.status === "filled" ? "pos" : "info"}>{t.status ?? "—"}</Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Run metadata">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[12px] sm:grid-cols-4">
          <Meta label="Started" value={fmtDate(equity?.meta?.started_at)} />
          <Meta label="Mode" value={equity?.meta?.mode ?? "—"} />
          <Meta label="IB endpoint" value={`${equity?.meta?.ib_host}:${equity?.meta?.ib_port}`} />
          <Meta label="Auto execute" value={equity?.meta?.auto_execute ? "ON" : "off"} />
        </dl>
      </Card>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wider text-muted">{label}</dt>
      <dd className="num mt-0.5 text-[13px] text-fg">{value}</dd>
    </div>
  );
}
