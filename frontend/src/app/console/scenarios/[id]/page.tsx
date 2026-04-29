"use client";
import { use, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft,
  BarChart3,
  Brain,
  CheckCircle2,
  Network,
  TrendingDown,
  TrendingUp,
  XCircle,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { StatTile } from "@/components/ui/StatTile";
import { useScenarios } from "@/lib/hooks";
import { fmtDate, fmtMoney, fmtNumber, fmtPct } from "@/lib/format";
import { getJSON, type ShockScenario } from "@/lib/api";

const fetcher = <T,>(path: string) => getJSON<T>(path);

interface ScenarioTrade {
  ticker: string;
  instrument: "equity" | "put_option" | "call_option";
  side: "LONG" | "SHORT";
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  shares: number;
  notional: number;
  pnl: number;
  pnl_pct: number;
  won: boolean;
  graph_path: string;
  rationale: string;
}

interface ScenarioDetail {
  scenario_id: string;
  name: string;
  start_date: string;
  end_date: string;
  initial_equity: number;
  final_equity: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  fno_trades: number;
  equity_trades: number;
  fno_pnl: number;
  cost_drag_pct: number;
  shock_type: string;
  trades?: ScenarioTrade[];
}

interface BacktestData {
  scenarios: ScenarioDetail[];
  [key: string]: unknown;
}

export default function ScenarioDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: scenariosData } = useScenarios();
  const { data: backtest, isLoading } = useSWR<BacktestData>(
    `/live/backtests/shock_driven_option_c_full`,
    fetcher,
  );

  const scenario: ShockScenario | undefined = scenariosData?.scenarios?.find(
    (s) => s.id === id,
  );
  const stats: ScenarioDetail | undefined = backtest?.scenarios?.find(
    (s) => s.scenario_id === id,
  );
  const trades = stats?.trades ?? [];

  const tradeStats = useMemo(() => {
    if (!trades.length) return null;
    const winners = trades.filter((t) => t.won);
    const losers = trades.filter((t) => !t.won);
    const avgWin =
      winners.length > 0
        ? winners.reduce((a, t) => a + t.pnl, 0) / winners.length
        : 0;
    const avgLoss =
      losers.length > 0
        ? losers.reduce((a, t) => a + t.pnl, 0) / losers.length
        : 0;
    const equityTrades = trades.filter((t) => t.instrument === "equity");
    const optionTrades = trades.filter((t) => t.instrument !== "equity");
    return { winners, losers, avgWin, avgLoss, equityTrades, optionTrades };
  }, [trades]);

  return (
    <div className="flex flex-col gap-6 fade-up">
      <Link
        href="/console/scenarios"
        className="inline-flex items-center gap-1.5 text-[12px] text-muted hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> All scenarios
      </Link>

      {/* ── Header ──────────────────────────────────── */}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {scenario?.expected_direction && (
            <Badge
              variant={
                scenario.expected_direction === "BEARISH"
                  ? "neg"
                  : scenario.expected_direction === "BULLISH"
                  ? "pos"
                  : "info"
              }
            >
              {scenario.expected_direction}
            </Badge>
          )}
          {scenario?.shock_type && (
            <Badge variant="neutral">{scenario.shock_type}</Badge>
          )}
        </div>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">
          {scenario?.name ?? id}
        </h1>
        <p className="text-[13px] text-muted">
          {scenario
            ? `${fmtDate(scenario.start_date)} → ${fmtDate(scenario.end_date)}`
            : ""}
        </p>
        {scenario?.description && (
          <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
            {scenario.description}
          </p>
        )}
      </header>

      {/* ── Causal chain ────────────────────────────── */}
      {scenario?.causal_chain && (
        <Card
          title="Causal chain"
          subtitle="LLM-traced impact path through Neo4j supply-chain graph"
        >
          <div className="flex items-start gap-3">
            <Network className="mt-0.5 h-5 w-5 shrink-0 text-brand" />
            <p className="text-[13px] leading-relaxed text-fg">
              {scenario.causal_chain}
            </p>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-subtle">
                Beneficiaries
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {(scenario.beneficiary_tickers ?? []).map((t) => (
                  <Badge key={t} variant="pos">
                    <span className="num">{t}</span>
                  </Badge>
                ))}
                {!(scenario.beneficiary_tickers?.length) && (
                  <span className="text-[11px] text-subtle">None identified</span>
                )}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-subtle">
                Victims
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {(scenario.victim_tickers ?? []).map((t) => (
                  <Badge key={t} variant="neg">
                    <span className="num">{t}</span>
                  </Badge>
                ))}
                {!(scenario.victim_tickers?.length) && (
                  <span className="text-[11px] text-subtle">None identified</span>
                )}
              </div>
            </div>
          </div>
        </Card>
      )}

      {isLoading && <div className="skeleton h-40" />}

      {/* ── KPIs ────────────────────────────────────── */}
      {stats && (
        <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <StatTile
            label="Return"
            value={fmtPct(stats.total_return_pct)}
            trend={stats.total_return_pct >= 0 ? "up" : "down"}
            hint={`${fmtMoney(stats.initial_equity, { compact: true })} → ${fmtMoney(stats.final_equity, { compact: true })}`}
            icon={
              stats.total_return_pct >= 0 ? (
                <TrendingUp className="h-4 w-4" />
              ) : (
                <TrendingDown className="h-4 w-4" />
              )
            }
          />
          <StatTile
            label="Sharpe ratio"
            value={stats.sharpe_ratio.toFixed(2)}
            trend={
              stats.sharpe_ratio > 1
                ? "up"
                : stats.sharpe_ratio < 0
                ? "down"
                : "neutral"
            }
            hint={
              stats.sharpe_ratio > 1
                ? "Above threshold"
                : stats.sharpe_ratio > 0
                ? "Positive"
                : "Negative"
            }
            icon={<BarChart3 className="h-4 w-4" />}
          />
          <StatTile
            label="Win rate"
            value={fmtPct(stats.win_rate, 1)}
            trend={stats.win_rate >= 40 ? "up" : "neutral"}
            hint={`${Math.round(stats.total_trades * stats.win_rate / 100)}/${stats.total_trades} trades`}
            icon={<CheckCircle2 className="h-4 w-4" />}
          />
          <StatTile
            label="Max drawdown"
            value={fmtPct(-stats.max_drawdown_pct)}
            trend={stats.max_drawdown_pct > 5 ? "down" : "neutral"}
            hint={`Cost drag: ${fmtPct(stats.cost_drag_pct)}`}
            icon={<TrendingDown className="h-4 w-4" />}
          />
        </section>
      )}

      {/* ── Trade breakdown ─────────────────────────── */}
      {stats && tradeStats && (
        <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <MiniStat
            label="Total trades"
            value={fmtNumber(stats.total_trades)}
          />
          <MiniStat
            label="Equity trades"
            value={fmtNumber(stats.equity_trades)}
          />
          <MiniStat
            label="Options trades"
            value={fmtNumber(stats.fno_trades)}
          />
          <MiniStat
            label="Avg winner"
            value={fmtMoney(tradeStats.avgWin)}
            accent="pos"
          />
          <MiniStat
            label="Avg loser"
            value={fmtMoney(tradeStats.avgLoss)}
            accent="neg"
          />
        </section>
      )}

      {/* ── Trade table ─────────────────────────────── */}
      {trades.length > 0 && (
        <Card
          title={`Trades · ${trades.length}`}
          subtitle="Every position the LLM entered — with graph-intelligence rationale"
        >
          <TradeTable trades={trades} />
        </Card>
      )}

      {!isLoading && trades.length === 0 && stats && (
        <Card>
          <Empty
            icon={<Brain className="h-8 w-8" />}
            title="Trade details loading"
            hint="Per-trade breakdown with LLM rationales will appear here."
          />
        </Card>
      )}
    </div>
  );
}

/* ─── Sub-components ──────────────────────────────────────────────── */

function MiniStat({
  label,
  value,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  accent?: "pos" | "neg";
}) {
  const cls =
    accent === "pos"
      ? "text-pos"
      : accent === "neg"
      ? "text-neg"
      : "text-fg";
  return (
    <div className="card p-4">
      <div className="text-[10px] uppercase tracking-[0.14em] text-muted">
        {label}
      </div>
      <div className={`num mt-1.5 text-xl font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

function TradeTable({ trades }: { trades: ScenarioTrade[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-app text-left text-[10px] uppercase tracking-wider text-muted">
            <th className="py-2 pr-3 font-medium">Ticker</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium">Side</th>
            <th className="py-2 pr-3 font-medium">Entry</th>
            <th className="py-2 pr-3 font-medium">Exit</th>
            <th className="py-2 pr-3 text-right font-medium">Entry $</th>
            <th className="py-2 pr-3 text-right font-medium">Exit $</th>
            <th className="py-2 pr-3 text-right font-medium">P&L</th>
            <th className="py-2 pr-3 text-right font-medium">%</th>
            <th className="py-2 font-medium">Result</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[hsl(var(--border))]">
          {trades.map((t, i) => (
            <TradeRow key={`${t.ticker}-${t.entry_date}-${i}`} trade={t} />
          ))}
        </tbody>
      </table>

      {/* ── Rationale cards ────────────────────── */}
      <div className="mt-6 space-y-3 border-t border-app pt-4">
        <div className="text-[11px] uppercase tracking-wider text-muted">
          LLM reasoning per trade
        </div>
        {trades.map((t, i) => (
          <div
            key={`rationale-${t.ticker}-${i}`}
            className={`rounded-lg border p-3 ${
              t.won
                ? "border-[hsl(var(--pos)/0.2)] bg-[hsl(var(--pos)/0.04)]"
                : "border-[hsl(var(--neg)/0.2)] bg-[hsl(var(--neg)/0.04)]"
            }`}
          >
            <div className="flex items-center gap-2">
              <Badge variant={t.won ? "pos" : "neg"}>
                <span className="num">{t.ticker}</span>
              </Badge>
              <span className="text-[11px] text-muted">
                {instrumentLabel(t.instrument)} · {t.side} · {fmtDate(t.entry_date)} → {fmtDate(t.exit_date)}
              </span>
              <span
                className={`num ml-auto text-[12px] font-semibold ${
                  t.pnl >= 0 ? "text-pos" : "text-neg"
                }`}
              >
                {fmtMoney(t.pnl)}
              </span>
            </div>
            {t.graph_path && (
              <div className="mt-2 flex items-start gap-1.5">
                <Network className="mt-0.5 h-3 w-3 shrink-0 text-brand" />
                <span className="text-[11px] leading-relaxed text-fg">
                  {t.graph_path}
                </span>
              </div>
            )}
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
              {t.rationale}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function TradeRow({ trade: t }: { trade: ScenarioTrade }) {
  const pnlCls = t.pnl >= 0 ? "text-pos" : "text-neg";
  return (
    <tr className="hover:bg-[hsl(var(--bg-sunken))]">
      <td className="py-2 pr-3 font-semibold text-fg">{t.ticker}</td>
      <td className="py-2 pr-3 text-muted">{instrumentLabel(t.instrument)}</td>
      <td className="py-2 pr-3">
        <Badge variant={t.side === "LONG" ? "pos" : "neg"}>{t.side}</Badge>
      </td>
      <td className="num py-2 pr-3 text-muted">{fmtDate(t.entry_date)}</td>
      <td className="num py-2 pr-3 text-muted">{fmtDate(t.exit_date)}</td>
      <td className="num py-2 pr-3 text-right text-fg">
        ${t.entry_price.toFixed(2)}
      </td>
      <td className="num py-2 pr-3 text-right text-fg">
        ${t.exit_price.toFixed(2)}
      </td>
      <td className={`num py-2 pr-3 text-right font-semibold ${pnlCls}`}>
        {fmtMoney(t.pnl)}
      </td>
      <td className={`num py-2 pr-3 text-right ${pnlCls}`}>
        {t.pnl_pct >= 0 ? "+" : ""}
        {t.pnl_pct.toFixed(1)}%
      </td>
      <td className="py-2">
        {t.won ? (
          <CheckCircle2 className="h-4 w-4 text-pos" />
        ) : (
          <XCircle className="h-4 w-4 text-neg" />
        )}
      </td>
    </tr>
  );
}

function instrumentLabel(inst: string): string {
  switch (inst) {
    case "equity":
      return "Equity";
    case "put_option":
      return "Put";
    case "call_option":
      return "Call";
    default:
      return inst;
  }
}
