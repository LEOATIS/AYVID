"use client";
import Link from "next/link";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  Briefcase,
  CircleDollarSign,
  ExternalLink,
  Gauge,
  LineChart,
  Network,
  Radio,
  Server,
  Zap,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { EquityChart } from "@/components/charts/EquityChart";
import {
  useBacktests,
  useEquity,
  useRunner,
  useScenarios,
  useSignals,
  useSystemStatus,
  usePipelineStatus,
} from "@/lib/hooks";
import {
  fmtAge,
  fmtDate,
  fmtMoneyAdaptive,
  fmtNumber,
  fmtPct,
  fmtTime,
  fmtUptime,
} from "@/lib/format";
import type { BacktestSummary, EquityResponse, PipelineStatus, ShockScenario, Signal } from "@/lib/api";

export default function Dashboard() {
  const { data: equity } = useEquity(500) as { data?: EquityResponse };
  const { data: runner } = useRunner();
  const { data: signals } = useSignals();
  const { data: backtests } = useBacktests();
  const { data: scenarios } = useScenarios();
  const { data: system } = useSystemStatus();
  const { data: pipeline } = usePipelineStatus();

  const summary = equity?.summary;
  const meta = equity?.meta;
  const initial = summary?.initial_equity ?? meta?.initial_equity ?? 0;
  const final = summary?.final_equity ?? initial;
  const ret = summary?.total_return_pct ?? 0;
  const dd = summary?.drawdown_from_peak_pct ?? 0;
  const positive = ret >= 0;

  const headlineBacktest = (backtests?.reports as BacktestSummary[] | undefined)?.find(
    (r) => r.kind === "shock_driven",
  );

  return (
    <div className="flex flex-col gap-8 fade-up">
      {/* ── Hero ─────────────────────────────────────────────── */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={runner?.is_alive ? "pos" : "neutral"}>
            <span className={`dot ${runner?.is_alive ? "dot-live pulse-live" : "dot-idle"}`} />
            {runner?.is_alive ? "Paper runner live" : "Paper runner idle"}
          </Badge>
          <Badge variant="info">{runner?.mode === "ib_paper" ? "IB Paper · 127.0.0.1:7497" : runner?.mode ?? "—"}</Badge>
          {meta?.auto_execute === false && <Badge variant="warn">Manual exec</Badge>}
        </div>
        <h1
          className="font-display font-medium leading-[1.05] tracking-tight text-fg"
          style={{ fontSize: "clamp(1.6rem, 3.4vw, 2.25rem)" }}
        >
          Live operations console
        </h1>
        <p className="max-w-3xl text-[13.5px] leading-relaxed text-muted sm:text-[14px]">
          AYVID — <span className="text-fg">Asymmetric Yield Via Intelligent Decisions</span>.
          Swarms of LLM agents read 10-Ks, earnings calls, and global news in
          real time, trace causal consequences through Neo4j supply-chain graphs,
          and route sized, risk-limited orders into Interactive Brokers. This is
          the live control surface.
        </p>
      </section>

      {/* ── KPIs ─────────────────────────────────────────────── */}
      <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
        <StatTile
          label="Equity"
          value={fmtMoneyAdaptive(final)}
          delta={fmtPct(ret)}
          trend={positive ? "up" : "down"}
          hint={`vs ${fmtMoneyAdaptive(initial)} start`}
          icon={<CircleDollarSign className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Drawdown from peak"
          value={fmtPct(dd)}
          trend={dd < -0.5 ? "down" : "neutral"}
          hint={`peak ${fmtMoneyAdaptive(summary?.peak_equity)}`}
          icon={<Gauge className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Open positions"
          value={fmtNumber(summary?.open_positions ?? 0)}
          hint={`${fmtNumber(signals?.count ?? 0)} active signals`}
          icon={<Briefcase className="h-4 w-4" />}
          loading={!summary}
        />
        <StatTile
          label="Runner uptime"
          value={runner?.uptime_seconds != null ? fmtUptime(runner.uptime_seconds) : "—"}
          hint={runner?.snapshots_total != null ? `${fmtNumber(runner.snapshots_total)} snapshots` : ""}
          icon={<Activity className="h-4 w-4" />}
          loading={!runner}
        />
        <StatTile
          label="Asset classes"
          value="6"
          hint="equities · FX · commodities · crypto · ETFs · bonds"
          icon={<BarChart3 className="h-4 w-4" />}
        />
      </section>

      {/* ── Main grid ────────────────────────────────────────── */}
      <section className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title={
            <span className="inline-flex items-center gap-2">
              Fund Performance
              <span
                className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[9px] font-mono uppercase tracking-[0.08em]"
                style={{
                  background: "hsl(var(--pos) / 0.10)",
                  border: "1px solid hsl(var(--pos) / 0.25)",
                  color: "hsl(var(--pos))",
                }}
              >
                <span className="dot dot-live pulse-live" />
                LIVE
              </span>
            </span>
          }
          subtitle={
            summary
              ? `${summary.snapshots_count} snapshots · ${fmtAge(runner?.last_snapshot_age_seconds)} · mode ${runner?.mode ?? "—"}`
              : "Awaiting first snapshot from paper runner"
          }
          right={
            summary ? (
              <div className="text-right">
                <div
                  className="num font-semibold leading-none"
                  style={{
                    fontSize: 22,
                    letterSpacing: "-0.03em",
                    color:
                      (summary.total_return_pct ?? 0) >= 0
                        ? "hsl(var(--pos))"
                        : "hsl(var(--neg))",
                  }}
                >
                  {(summary.total_return_pct ?? 0) >= 0 ? "+" : ""}
                  {(summary.total_return_pct ?? 0).toFixed(3)}%
                </div>
                <div className="text-[10px] text-subtle">since inception</div>
              </div>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-[11px]">
                <Radio className="h-3.5 w-3.5" />
                {fmtAge(runner?.last_snapshot_age_seconds)}
              </span>
            )
          }
        >
          <EquityChart
            snapshots={equity?.snapshots ?? []}
            initial={equity?.summary?.initial_equity ?? initial ?? 1_000_000}
            height={300}
            showGrid
          />
        </Card>

        <Card title="System health" subtitle="API · runner · graph">
          <SystemPanel system={system} runner={runner} />
        </Card>
      </section>

      {/* ── Pipeline status ──────────────────────────────────── */}
      <section className="grid grid-cols-1 gap-5">
        <Card
          title="Pipeline status"
          subtitle="What the engine is doing right now"
          right={
            <span className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              <Radio className="h-3.5 w-3.5" />
              {pipeline?.snapshot_at ? fmtAge(Math.floor((Date.now() - new Date(pipeline.snapshot_at).getTime()) / 1000)) : "—"}
            </span>
          }
        >
          <PipelineStatusPanel data={pipeline} />
        </Card>
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="Headline backtest"
          subtitle="Naive vs. shock-driven — same data, same costs"
          right={
            <Link
              href="/console/backtests"
              className="inline-flex items-center gap-1 text-brand hover:underline"
            >
              All reports <ArrowUpRight className="h-3 w-3" />
            </Link>
          }
        >
          <BacktestSpotlight reports={backtests?.reports} />
        </Card>

        <Card
          title="Recent signals"
          subtitle="From the active signal book"
          right={
            <Link
              href="/console/signals"
              className="inline-flex items-center gap-1 text-brand hover:underline"
            >
              All <ArrowUpRight className="h-3 w-3" />
            </Link>
          }
        >
          <SignalsList signals={(signals?.signals as Signal[] | undefined)?.slice(0, 5) ?? []} />
        </Card>
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="Shock catalog"
          subtitle="Named historical events the engine trades against"
          right={
            <Link
              href="/console/scenarios"
              className="inline-flex items-center gap-1 text-brand hover:underline"
            >
              All scenarios <ArrowUpRight className="h-3 w-3" />
            </Link>
          }
        >
          <ScenariosPreview scenarios={scenarios?.scenarios ?? []} />
        </Card>

        <Card title="Quick links" subtitle="Jump to common workflows">
          <QuickLinks headline={headlineBacktest} />
        </Card>
      </section>
    </div>
  );
}

/* ─── Sub-components ──────────────────────────────────────────────── */

function PipelineStatusPanel({ data }: { data?: PipelineStatus }) {
  if (!data) {
    return <Empty title="Pipeline telemetry unavailable" hint="Worker hasn't reported yet" />;
  }
  const allClasses = ["EQUITY", "ETF", "INDEX", "FX", "FUTURE", "COMMODITY", "CRYPTO", "OPTION"] as const;
  const classColor: Record<string, string> = {
    EQUITY: "var(--brand)",
    ETF: "var(--info)",
    INDEX: "var(--brand)",
    FX: "var(--pos)",
    FUTURE: "var(--warn)",
    COMMODITY: "var(--warn)",
    CRYPTO: "var(--warn)",
    OPTION: "var(--neg)",
  };
  const isLLM = data.orchestration_mode === "llm_swarm";
  const isLive = data.execution_mode === "ib_live";
  const isPaper = data.execution_mode === "ib_paper";
  return (
    <div className="flex flex-col gap-4">
      {/* Mode badges */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-subtle">Signal engine</span>
          <Badge variant={isLLM ? "info" : "neutral"}>
            {isLLM ? "◆ LLM swarm" : "◇ Rule-based"}
          </Badge>
          <span className="text-[11px] text-muted">
            timeout {data.llm_signal_timeout_seconds}s · max {data.llm_max_concurrent_signals} concurrent
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-subtle">Execution mode</span>
          <Badge variant={isLive ? "neg" : isPaper ? "pos" : "info"}>
            {isLive ? "⚠ IB live" : isPaper ? "● IB paper" : "○ Shadow"}
          </Badge>
          <span className="text-[11px] text-muted">
            auto-execute {data.auto_execute ? "ON" : "off"}
            {isLive ? ` · confirmed ${data.live_confirmed ? "✓" : "✗"}` : ""}
          </span>
        </div>
      </div>

      {/* Counts */}
      <div className="grid grid-cols-2 gap-3 border-y border-[hsl(var(--border))] py-3 sm:grid-cols-4">
        {[
          { label: "Signals ready", value: data.signals_ready },
          { label: "Waiting", value: data.signals_waiting },
          { label: "Open positions", value: data.open_positions },
          { label: "Trades today", value: data.trades_today },
        ].map((s) => (
          <div key={s.label} className="text-center">
            <div className="num text-[18px] font-semibold text-fg">{fmtNumber(s.value)}</div>
            <div className="text-[10px] uppercase tracking-wider text-subtle">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Asset classes seen in the live signal book */}
      <div className="flex flex-wrap gap-1.5">
        {allClasses.map((ac) => {
          const active = data.asset_classes_enabled.includes(ac);
          return (
            <span
              key={ac}
              className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-mono"
              style={{
                background: active ? `color-mix(in srgb, ${classColor[ac]} 12%, transparent)` : "hsl(var(--bg-sunken))",
                border: `1px solid ${active ? `color-mix(in srgb, ${classColor[ac]} 35%, transparent)` : "hsl(var(--border))"}`,
                color: active ? classColor[ac] : "hsl(var(--fg-muted))",
                fontWeight: active ? 600 : 400,
              }}
            >
              {active ? "●" : "○"} {ac}
            </span>
          );
        })}
      </div>

      {/* Last cycle */}
      <div className="text-[11px] text-muted">
        Last cycle: {data.last_pipeline_run ? fmtTime(data.last_pipeline_run) : "never"} ·
        {" "}{fmtNumber(data.signals_generated_last_cycle)} signals minted
      </div>
    </div>
  );
}

function SystemPanel({
  system,
  runner,
}: {
  system: ReturnType<typeof useSystemStatus>["data"] | undefined;
  runner: ReturnType<typeof useRunner>["data"] | undefined;
}) {
  const neo4jOk = system?.neo4j?.ok === true;
  const neo4jStats = system?.neo4j?.stats;
  const neo4jLine = neo4jOk
    ? neo4jStats && Object.keys(neo4jStats).length
      ? Object.entries(neo4jStats)
          .slice(0, 2)
          .map(([k, v]) => `${fmtNumber(v as number)} ${k}`)
          .join(" · ")
      : "online"
    : system?.neo4j?.error
    ? `down · ${system.neo4j.error.slice(0, 40)}`
    : "—";

  const llmOk = system?.llm?.ok === true;
  const llmLine = system?.llm?.provider
    ? `${system.llm.provider}${system.llm.model ? ` · ${system.llm.model}` : ""}`
    : "—";

  const rows: {
    label: string;
    value: React.ReactNode;
    hint?: string;
    status?: "ok" | "warn" | "down";
  }[] = [
    {
      label: "API",
      value: <span className="num text-fg">FastAPI · :8000</span>,
      status: system?.api === "ok" ? "ok" : "down",
    },
    {
      label: "Paper runner",
      value: (
        <span className="num text-fg">
          {runner?.is_alive ? "live" : "idle"}
          <span className="text-muted"> · {runner?.mode ?? "—"}</span>
        </span>
      ),
      hint:
        runner?.last_snapshot_age_seconds != null
          ? `last tick ${fmtAge(runner.last_snapshot_age_seconds)}`
          : undefined,
      status: runner?.is_alive ? "ok" : "warn",
    },
    {
      label: "Neo4j graph",
      value: <span className="num text-fg">{neo4jLine}</span>,
      hint: system?.neo4j?.uri ? "Aura · neo4j+s://" : undefined,
      status: neo4jOk ? "ok" : "down",
    },
    {
      label: "LLM provider",
      value: <span className="num text-fg">{llmLine}</span>,
      hint: system?.llm?.has_key ? "key wired" : "no key",
      status: llmOk ? "ok" : "warn",
    },
    {
      label: "Auto-execute",
      value: (
        <span className="num text-fg">
          {system?.config?.auto_execute ? "ON" : "off"}
        </span>
      ),
      hint: system?.config?.auto_execute ? "live orders" : "human in loop",
      status: system?.config?.auto_execute ? "ok" : "warn",
    },
  ];

  const dotCls = (s?: "ok" | "warn" | "down") =>
    s === "ok" ? "dot-live" : s === "warn" ? "dot-warn" : "dot-down";

  return (
    <ul className="space-y-3">
      {rows.map((r) => (
        <li key={r.label} className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-[13px] text-muted">
            <span className={`dot ${dotCls(r.status)}`} />
            {r.label}
          </span>
          <span className="text-right text-[13px] leading-tight">
            {r.value}
            {r.hint && (
              <span className="block text-[10px] text-subtle">{r.hint}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function BacktestSpotlight({ reports }: { reports?: BacktestSummary[] }) {
  if (!reports?.length) {
    return <Empty icon={<BarChart3 className="h-8 w-8" />} title="No backtests yet" />;
  }
  const naive = reports.find((r) => r.name === "walkforward_report_loose");
  const shock = reports.find((r) => r.kind === "shock_driven");
  const headline = shock ?? reports[0];

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <BacktestCard
          title="Shock-driven (AYVID's mode)"
          subtitle="Trade only inside graph-surfaced shock windows"
          report={shock ?? headline}
          accent="pos"
        />
        <BacktestCard
          title="Naive walk-forward (control)"
          subtitle="Trade everything every day on technicals"
          report={naive}
          accent="neg"
        />
      </div>
      <BacktestComparisonTable shock={shock} naive={naive} />
    </div>
  );
}

function BacktestComparisonTable({
  shock,
  naive,
}: {
  shock?: BacktestSummary;
  naive?: BacktestSummary;
}) {
  if (!shock && !naive) return null;
  const rows: { label: string; key: keyof BacktestSummary; isPct: boolean; lowerIsBetter?: boolean }[] = [
    { label: "Compounded return", key: "compounded_return_pct", isPct: true },
    { label: "Pooled WR", key: "pooled_win_rate", isPct: true },
    { label: "Avg Sharpe", key: "avg_sharpe", isPct: false },
    { label: "Worst DD", key: "worst_drawdown_pct", isPct: true, lowerIsBetter: true },
    { label: "Cost drag", key: "avg_cost_drag_pct", isPct: true, lowerIsBetter: true },
  ];
  const fmtCell = (v: number | null | undefined, isPct: boolean): string => {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    if (isPct) {
      const sign = v > 0 ? "+" : "";
      return `${sign}${v.toFixed(2)}%`;
    }
    return v.toFixed(2);
  };
  // Color cells by which side wins per metric. lowerIsBetter rows
  // invert the comparison.
  const better = (a: number | null | undefined, b: number | null | undefined, lowerWins: boolean) => {
    if (a == null || b == null) return null;
    if (a === b) return null;
    return lowerWins ? a < b : a > b;
  };
  return (
    <div className="card-soft overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-app text-left text-[10px] uppercase tracking-wider text-muted">
            <th className="px-4 py-2.5 font-medium">Mode</th>
            {rows.map((r) => (
              <th key={r.label} className="px-3 py-2.5 text-right font-medium">
                {r.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[hsl(var(--border))]">
          <tr>
            <td className="px-4 py-2.5">
              <span className="text-fg">Naive (trade every ticker daily)</span>
            </td>
            {rows.map((r) => {
              const v = naive?.[r.key] as number | null | undefined;
              const o = shock?.[r.key] as number | null | undefined;
              const isWinner = better(v, o, !!r.lowerIsBetter);
              const cls =
                isWinner === true
                  ? "text-pos"
                  : isWinner === false
                  ? "text-neg"
                  : "text-fg";
              return (
                <td key={r.label} className={`num px-3 py-2.5 text-right ${cls}`}>
                  {fmtCell(v, r.isPct)}
                </td>
              );
            })}
          </tr>
          <tr>
            <td className="px-4 py-2.5">
              <span className="text-fg">Shock-driven (AYVID&apos;s mode)</span>
            </td>
            {rows.map((r) => {
              const v = shock?.[r.key] as number | null | undefined;
              const o = naive?.[r.key] as number | null | undefined;
              const isWinner = better(v, o, !!r.lowerIsBetter);
              const cls =
                isWinner === true
                  ? "text-pos"
                  : isWinner === false
                  ? "text-neg"
                  : "text-fg";
              return (
                <td key={r.label} className={`num px-3 py-2.5 text-right ${cls}`}>
                  {fmtCell(v, r.isPct)}
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function BacktestCard({
  title,
  subtitle,
  report,
  accent,
}: {
  title: string;
  subtitle: string;
  report?: BacktestSummary;
  accent: "pos" | "neg";
}) {
  if (!report) {
    return (
      <div className="card-soft p-4">
        <div className="text-[12px] font-medium text-fg">{title}</div>
        <div className="mt-1 text-[11px] text-muted">{subtitle}</div>
        <div className="mt-3 text-[12px] text-subtle">No data</div>
      </div>
    );
  }
  const ret = report.compounded_return_pct ?? report.total_return_pct ?? 0;
  const cagr = report.cagr_pct;
  const cls = ret >= 0 ? "text-pos" : "text-neg";
  return (
    <Link
      href={`/console/backtests/${report.name}`}
      className="card-soft block p-4 transition hover:border-strong hover:bg-[hsl(var(--bg-sunken))]"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[12px] font-medium text-fg">{title}</div>
          <div className="mt-0.5 text-[11px] text-muted">{subtitle}</div>
        </div>
        <Badge variant={accent}>{report.kind}</Badge>
      </div>
      <div className="mt-3 flex items-end gap-3">
        <div className={`num text-3xl font-semibold tracking-tight ${cls}`}>
          {fmtPct(ret)}
        </div>
        {cagr != null && (
          <div className="text-[11px] text-muted">
            <span className="num text-fg">{fmtPct(cagr)}</span> CAGR
          </div>
        )}
      </div>
      <div className="mt-2 text-[11px] text-muted">
        {report.span} · {fmtNumber(report.pooled_trades ?? null)} trades
      </div>
    </Link>
  );
}

function SignalsList({ signals }: { signals: Signal[] }) {
  if (!signals.length) {
    return (
      <Empty
        icon={<LineChart className="h-8 w-8" />}
        title="No active signals"
        hint="The signal book is empty or waiting on the next pipeline cycle."
      />
    );
  }
  return (
    <ul className="divide-y divide-[hsl(var(--border))]">
      {signals.map((s, idx) => {
        const long = s.direction === "LONG";
        return (
          <li key={`${s.ticker}-${idx}`} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="num text-[13px] font-semibold tracking-tight text-fg">
                  {s.ticker}
                </span>
                <Badge variant={long ? "pos" : "neg"}>{s.direction}</Badge>
                <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${
                  s.instrument_type === "EQUITY" ? "bg-blue-500/20 text-blue-400" :
                  s.instrument_type === "FX" ? "bg-green-500/20 text-green-400" :
                  s.instrument_type === "CRYPTO" ? "bg-pink-500/20 text-pink-400" :
                  s.instrument_type === "ETF" ? "bg-cyan-500/20 text-cyan-400" :
                  "bg-amber-500/20 text-amber-400"
                }`}>
                  {s.instrument_type || "EQUITY"}
                </span>
              </div>
              {s.rationale && (
                <p className="mt-0.5 truncate text-[11px] text-muted">{s.rationale}</p>
              )}
            </div>
            <div className="text-right">
              <div className="num text-[12px] font-medium text-fg">
                {(s.conviction * 100).toFixed(0)}%
              </div>
              <div className="text-[10px] text-subtle">conviction</div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function ScenariosPreview({ scenarios }: { scenarios: ShockScenario[] }) {
  if (!scenarios.length) {
    return <Empty icon={<Network className="h-8 w-8" />} title="Catalog empty" />;
  }
  const top = scenarios.slice(-6).reverse();
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {top.map((s) => (
        <Link
          key={s.id}
          href={`/console/scenarios#${s.id}`}
          className="card-soft block p-3 transition hover:border-strong hover:bg-[hsl(var(--bg-sunken))]"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium text-fg">{s.name}</div>
              <div className="text-[11px] text-muted">
                {fmtDate(s.start_date)} · {s.tickers.length} tickers
              </div>
            </div>
            <Badge variant={s.expected_direction === "BEARISH" ? "neg" : s.expected_direction === "BULLISH" ? "pos" : "info"}>
              {s.expected_direction ?? s.shock_type ?? "shock"}
            </Badge>
          </div>
        </Link>
      ))}
    </div>
  );
}

function QuickLinks({ headline }: { headline?: BacktestSummary }) {
  const links: { href: string; label: string; icon: React.ReactNode; hint?: string }[] = [
    { href: "/console/live-shock", label: "Live shock demo", icon: <Zap className="h-4 w-4" />, hint: "60-sec demo screen" },
    { href: "/console/portfolio", label: "Portfolio & positions", icon: <Briefcase className="h-4 w-4" /> },
    { href: "/console/signals", label: "Signal book", icon: <LineChart className="h-4 w-4" /> },
    { href: "/console/backtests", label: "Backtest reports", icon: <BarChart3 className="h-4 w-4" />, hint: headline ? `latest: ${fmtPct(headline.compounded_return_pct ?? headline.total_return_pct ?? 0)}` : undefined },
    { href: "/console/scenarios", label: "Shock catalog", icon: <Network className="h-4 w-4" /> },
    { href: "/console/portfolio", label: "Multi-Asset Allocation", icon: <BarChart3 className="h-4 w-4" /> },
  ];
  return (
    <ul className="space-y-1">
      {links.map((l) => (
        <li key={`${l.href}::${l.label}`}>
          <Link
            href={l.href}
            className="flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-[13px] text-fg hover:bg-[hsl(var(--bg-sunken))]"
          >
            <span className="inline-flex items-center gap-2">
              <span className="text-muted">{l.icon}</span>
              {l.label}
            </span>
            <span className="text-[11px] text-muted">
              {l.hint ?? <ExternalLink className="h-3.5 w-3.5" />}
            </span>
          </Link>
        </li>
      ))}
      <li className="px-2.5 pt-2 text-[11px] text-subtle">
        <span className="inline-flex items-center gap-1.5">
          <Server className="h-3 w-3" />
          API · /api/v1/live/*
        </span>
      </li>
    </ul>
  );
}
