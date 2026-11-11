"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  BarChart3,
  Briefcase,
  ExternalLink,
  LineChart,
  Network,
  Server,
  Zap,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
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

/**
 * AYVID console overview — newspaper masthead + holdings statement, per brief §5.3.
 *
 * Layout rhythm:
 *  1. Section header treated as a newspaper masthead.
 *     - .label-mono eyebrow: "LIVE OPERATIONS"
 *     - .display-section title: "Asymmetric Yield Via Intelligent Decisions."
 *     - .body-prose subheading: one sentence describing the mission
 *     - Horizontal rule, then live status badges below.
 *  2. KPI strip — row of text, no card backgrounds (matches the Hero
 *     stat pattern).  Numbers HUGE in .data-mono, labels TINY in
 *     .label-mono, dividers HAIRLINE between columns.
 *  3. Fund Performance chart — no card border, only a top hairline rule.
 *  4. System Health — table-as-holdings-statement.  Two columns: label
 *     (left, Inter 13px var(--fg-2)) · value (right, mono 13px var(--fg)).
 *     6px coloured dot left of value: var(--pos)/var(--warn)/var(--neg).
 *
 * Every existing hook call + data shape is preserved.  Only the JSX/
 * className surface changes.
 */
export default function Dashboard() {
  const { data: equity } = useEquity(10000) as { data?: EquityResponse };
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
  const cumulativePnl = final - initial;
  const inceptionIso = summary?.first_timestamp ?? meta?.started_at ?? null;
  const inceptionShort = inceptionIso
    ? new Date(inceptionIso).toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : null;
  const inceptionRange = inceptionShort ? `${inceptionShort} → today` : "since inception";

  const headlineBacktest = (backtests?.reports as BacktestSummary[] | undefined)?.find(
    (r) => r.kind === "shock_driven",
  );

  return (
    <div
      className="flex flex-col"
      style={{
        gap: 48,
        // CRITICAL: min-width: 0 + overflow-x: hidden prevent long
        // unbroken signal rationales (e.g. "[SHOCK SAFETY HOLD:
        // cycle_1_shock] Technicals say LONG but news implies…")
        // from pushing the page wider than the viewport.  Without
        // this, the page acquired a horizontal scrollbar whenever
        // any signal's rationale didn't contain a whitespace
        // break-point within ~80 chars.  Belt-and-suspenders with
        // the per-item min-width: 0 below and the rationale's
        // line-clamp.
        minWidth: 0,
        maxWidth: "100%",
        overflowX: "hidden",
      }}
    >
      {/* ── Masthead ─────────────────────────────────────────── */}
      <section>
        <p className="label-mono" style={{ color: "hsl(var(--brand))" }}>
          Live operations
        </p>
        <h1
          className="display-section"
          style={{ marginTop: 16, color: "hsl(var(--fg))" }}
        >
          Asymmetric Yield Via Intelligent Decisions.
        </h1>
        <p className="body-prose" style={{ marginTop: 16 }}>
          Swarms of LLM agents read 10-Ks, earnings calls, and global news in
          real time, trace causal consequences through Neo4j supply-chain graphs,
          and route sized, risk-limited orders into Interactive Brokers.
          This is the live control surface.
        </p>
        <div
          aria-hidden
          style={{
            marginTop: 32,
            height: 1,
            background: "var(--rule-bold)",
            width: "100%",
          }}
        />
        <div
          className="flex flex-wrap items-center"
          style={{ marginTop: 24, gap: 12 }}
        >
          <StatusBadge
            on={runner?.is_alive ?? false}
            label={runner?.is_alive ? "Paper runner live" : "Paper runner idle"}
          />
          <span
            className="label-mono"
            style={{ color: "hsl(var(--fg-muted))" }}
          >
            {runner?.mode === "ib_paper"
              ? "IB Paper · 127.0.0.1:7497"
              : runner?.mode ?? "—"}
          </span>
          {meta?.auto_execute === false && (
            <Badge variant="warn">Manual exec</Badge>
          )}
        </div>
      </section>

      {/* ── KPI row-of-text ──────────────────────────────────── */}
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(6, 1fr)",
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
        className="kpi-strip"
      >
        <KpiCell
          first
          label="Equity"
          value={fmtMoneyAdaptive(final)}
          delta={`${positive ? "+" : ""}${fmtPct(ret)}`}
          deltaPositive={positive}
          hint={`vs ${fmtMoneyAdaptive(initial)} start`}
        />
        <KpiCell
          label={`ROI · ${inceptionRange}`}
          value={fmtPct(ret)}
          delta={`P&L ${fmtMoneyAdaptive(cumulativePnl)}`}
          deltaPositive={cumulativePnl >= 0}
          hint={`${fmtNumber(summary?.snapshots_count ?? 0)} snapshots`}
        />
        <KpiCell
          label="Drawdown from peak"
          value={fmtPct(dd)}
          hint={`peak ${fmtMoneyAdaptive(summary?.peak_equity)}`}
        />
        <KpiCell
          label="Open positions"
          value={fmtNumber(summary?.open_positions ?? 0)}
          hint={`${fmtNumber(signals?.count ?? 0)} active signals`}
        />
        <KpiCell
          label="Runner uptime"
          value={
            runner?.uptime_seconds != null ? fmtUptime(runner.uptime_seconds) : "—"
          }
          hint={
            runner?.snapshots_total != null
              ? `${fmtNumber(runner.snapshots_total)} snapshots`
              : ""
          }
        />
        <KpiCell
          label="Asset classes"
          value="6"
          hint="equities · FX · commodities · crypto · ETFs · bonds"
        />
      </section>

      {/* ── Fund Performance ──────────────────────────────────
          Chart sits directly on the page background — no card border,
          only a top hairline above the title (brief §5.3). */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <header
          className="flex flex-wrap items-baseline justify-between"
          style={{ gap: 16 }}
        >
          <div>
            <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
              Paper portfolio performance
            </p>
            <p
              className="body-prose"
              style={{ marginTop: 8, maxWidth: "60ch" }}
            >
              {summary
                ? `${summary.snapshots_count} snapshots · ${fmtAge(
                    runner?.last_snapshot_age_seconds,
                  )} · mode ${runner?.mode ?? "—"}`
                : "Awaiting first snapshot from paper runner"}
            </p>
          </div>
          {summary && (
            <CountedReturn ret={summary.total_return_pct ?? 0} range={inceptionRange} />
          )}
        </header>
        <div style={{ marginTop: 24 }}>
          <EquityChart
            snapshots={equity?.snapshots ?? []}
            initial={equity?.summary?.initial_equity ?? initial ?? 1_000_000}
            height={320}
            showGrid
          />
        </div>
      </section>

      {/* ── System Health — holdings statement format ───────── */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <header
          className="flex flex-wrap items-baseline justify-between"
          style={{ gap: 16 }}
        >
          <div>
            <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
              System health
            </p>
            <p className="body-prose" style={{ marginTop: 8 }}>
              API · runner · graph · LLM provider.
            </p>
          </div>
        </header>
        <SystemPanel system={system} runner={runner} />
      </section>

      {/* ── Pipeline status ──────────────────────────────────── */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <header className="flex flex-wrap items-baseline justify-between" style={{ gap: 16 }}>
          <div>
            <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
              Pipeline status
            </p>
            <p className="body-prose" style={{ marginTop: 8 }}>
              What the engine is doing right now.
            </p>
          </div>
          <span
            className="label-mono"
            style={{ color: "hsl(var(--fg-muted))" }}
          >
            {pipeline?.snapshot_at
              ? fmtAge(Math.floor((Date.now() - new Date(pipeline.snapshot_at).getTime()) / 1000))
              : "—"}
          </span>
        </header>
        <div style={{ marginTop: 24 }}>
          <PipelineStatusPanel data={pipeline} />
        </div>
      </section>

      {/* ── Headline backtest + Recent signals ──────────────── */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
          display: "grid",
          gridTemplateColumns: "1fr",
          gap: 32,
        }}
        className="two-col"
      >
        <div>
          <header className="flex flex-wrap items-baseline justify-between" style={{ gap: 16 }}>
            <div>
              <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
                Headline backtest
              </p>
              <p className="body-prose" style={{ marginTop: 8 }}>
                Naive vs. shock-driven — same data, same costs.
              </p>
            </div>
            <Link
              href="/console/backtests"
              className="label-mono inline-flex items-center"
              style={{ gap: 4, color: "hsl(var(--brand))" }}
            >
              All reports <ArrowUpRight className="h-3 w-3" />
            </Link>
          </header>
          <div style={{ marginTop: 24 }}>
            <BacktestSpotlight reports={backtests?.reports} />
          </div>
        </div>

        <div>
          <header className="flex flex-wrap items-baseline justify-between" style={{ gap: 16 }}>
            <div>
              <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
                Recent signals
              </p>
              <p className="body-prose" style={{ marginTop: 8 }}>
                From the active signal book.
              </p>
            </div>
            <Link
              href="/console/signals"
              className="label-mono inline-flex items-center"
              style={{ gap: 4, color: "hsl(var(--brand))" }}
            >
              All <ArrowUpRight className="h-3 w-3" />
            </Link>
          </header>
          <div style={{ marginTop: 24 }}>
            <SignalsList signals={(signals?.signals as Signal[] | undefined)?.slice(0, 5) ?? []} />
          </div>
        </div>
      </section>

      {/* ── Shock catalog + Quick links ──────────────────────── */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
          display: "grid",
          gridTemplateColumns: "1fr",
          gap: 32,
        }}
        className="two-col"
      >
        <div>
          <header className="flex flex-wrap items-baseline justify-between" style={{ gap: 16 }}>
            <div>
              <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
                Shock catalog
              </p>
              <p className="body-prose" style={{ marginTop: 8 }}>
                Named historical events the engine trades against.
              </p>
            </div>
            <Link
              href="/console/scenarios"
              className="label-mono inline-flex items-center"
              style={{ gap: 4, color: "hsl(var(--brand))" }}
            >
              All scenarios <ArrowUpRight className="h-3 w-3" />
            </Link>
          </header>
          <div style={{ marginTop: 24 }}>
            <ScenariosPreview scenarios={scenarios?.scenarios ?? []} />
          </div>
        </div>

        <div>
          <header className="flex flex-wrap items-baseline justify-between" style={{ gap: 16 }}>
            <div>
              <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
                Quick links
              </p>
              <p className="body-prose" style={{ marginTop: 8 }}>
                Jump to common workflows.
              </p>
            </div>
          </header>
          <div style={{ marginTop: 24 }}>
            <QuickLinks headline={headlineBacktest} />
          </div>
        </div>
      </section>

      <style jsx>{`
        /* CSS Grid items default to min-width: auto, which evaluates
           to min-content for grid tracks containing flex-wrap or
           long unbroken strings.  That lets the right-hand "Recent
           signals" column (1fr of a 2fr+1fr grid) push wider than
           its share whenever a signal rationale lacks a whitespace
           break-point.  Setting min-width: 0 on every direct child
           of .two-col restores the intended fr-share behaviour. */
        :global(.two-col > *) {
          min-width: 0;
        }
        @media (min-width: 1024px) {
          :global(.two-col) {
            grid-template-columns: 2fr 1fr !important;
          }
        }
        @media (max-width: 900px) {
          :global(.kpi-strip) {
            grid-template-columns: repeat(3, 1fr) !important;
            row-gap: 24px;
          }
          :global(.kpi-strip > div:nth-child(3n + 1)) {
            border-left: none !important;
            padding-left: 0 !important;
          }
        }
        @media (max-width: 540px) {
          :global(.kpi-strip) {
            grid-template-columns: repeat(2, 1fr) !important;
          }
          :global(.kpi-strip > div:nth-child(2n + 1)) {
            border-left: none !important;
            padding-left: 0 !important;
          }
        }
      `}</style>
    </div>
  );
}

/* ─── Cells & sub-components ───────────────────────────────────────── */

/** One column of the KPI strip — big number, tiny label, optional delta. */
function KpiCell({
  label,
  value,
  delta,
  deltaPositive,
  hint,
  first,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaPositive?: boolean;
  hint?: string;
  first?: boolean;
}) {
  return (
    <div
      style={{
        paddingLeft: first ? 0 : 20,
        paddingRight: 20,
        borderLeft: first ? "none" : "1px solid var(--rule)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        minWidth: 0,
      }}
    >
      <span
        className="data-mono"
        style={{
          fontSize: "clamp(22px, 2.4vw, 32px)",
          lineHeight: 1.05,
          fontWeight: 600,
          color: "hsl(var(--fg))",
          letterSpacing: "-0.01em",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
      {delta && (
        <span
          className="data-mono"
          style={{
            fontSize: 11,
            color: deltaPositive ? "hsl(var(--pos))" : "hsl(var(--neg))",
          }}
        >
          {delta}
        </span>
      )}
      <span className="label-mono" style={{ marginTop: 4 }}>
        {label}
      </span>
      {hint && (
        <span
          style={{
            fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
            fontSize: 11,
            lineHeight: 1.4,
            color: "hsl(var(--fg-subtle))",
          }}
        >
          {hint}
        </span>
      )}
    </div>
  );
}

/** Live status badge — small dot + label.  Forest dot when on, muted
 *  when off.  Replaces the previous .pill-status with halo. */
function StatusBadge({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className="inline-flex items-center"
      style={{
        gap: 8,
        fontFamily: 'var(--font-mono), "JetBrains Mono", ui-monospace, monospace',
        fontSize: 10,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        color: on ? "hsl(var(--pos))" : "hsl(var(--fg-subtle))",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: 9999,
          background: on ? "hsl(var(--pos))" : "hsl(var(--fg-subtle))",
        }}
      />
      {label}
    </span>
  );
}

/** Animated count-up for the return % at the top-right of the chart. */
function CountedReturn({ ret, range }: { ret: number; range: string }) {
  const [display, setDisplay] = useState(ret);
  const startedAt = useRef<number | null>(null);
  const fromRef = useRef(display);

  useEffect(() => {
    fromRef.current = display;
    startedAt.current = null;
    const target = ret;
    const duration = 480; // var(--t-settle)
    const ease = (t: number) => 1 - Math.pow(1 - t, 3); // ease-paper-ish

    let raf = 0;
    const tick = (ts: number) => {
      if (startedAt.current == null) startedAt.current = ts;
      const elapsed = ts - startedAt.current;
      const p = Math.min(1, elapsed / duration);
      setDisplay(fromRef.current + (target - fromRef.current) * ease(p));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ret]);

  const positive = display >= 0;
  return (
    <div className="text-right">
      <div
        className="data-mono"
        style={{
          fontSize: 28,
          fontWeight: 600,
          letterSpacing: "-0.02em",
          color: positive ? "hsl(var(--pos))" : "hsl(var(--neg))",
        }}
      >
        {positive ? "+" : ""}
        {display.toFixed(3)}%
      </div>
      <div
        className="label-mono"
        style={{ marginTop: 4, color: "hsl(var(--fg-subtle))" }}
      >
        {range}
      </div>
    </div>
  );
}

function PipelineStatusPanel({ data }: { data?: PipelineStatus }) {
  if (!data) {
    return <Empty title="Pipeline telemetry unavailable" hint="Worker hasn't reported yet" />;
  }
  const allClasses = ["EQUITY", "ETF", "INDEX", "FX", "FUTURE", "COMMODITY", "CRYPTO", "OPTION"] as const;
  const isLLM = data.orchestration_mode === "llm_swarm";
  const isLive = data.execution_mode === "ib_live";
  const isPaper = data.execution_mode === "ib_paper";
  return (
    <div className="flex flex-col" style={{ gap: 24 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 20,
        }}
      >
        <div className="flex flex-col" style={{ gap: 6 }}>
          <span className="label-mono">Signal engine</span>
          <span
            className="data-mono"
            style={{
              fontSize: 14,
              color: "hsl(var(--fg))",
            }}
          >
            {isLLM ? "LLM swarm" : "Rule-based"}
          </span>
          <span
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 11,
              color: "hsl(var(--fg-subtle))",
            }}
          >
            timeout {data.llm_signal_timeout_seconds}s · max{" "}
            {data.llm_max_concurrent_signals} concurrent
          </span>
        </div>
        <div className="flex flex-col" style={{ gap: 6 }}>
          <span className="label-mono">Execution mode</span>
          <span
            className="data-mono"
            style={{
              fontSize: 14,
              color: isLive
                ? "hsl(var(--neg))"
                : isPaper
                ? "hsl(var(--pos))"
                : "hsl(var(--fg))",
            }}
          >
            {isLive ? "IB live" : isPaper ? "IB paper" : "Shadow"}
          </span>
          <span
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 11,
              color: "hsl(var(--fg-subtle))",
            }}
          >
            auto-execute {data.auto_execute ? "on" : "off"}
            {isLive ? ` · confirmed ${data.live_confirmed ? "yes" : "no"}` : ""}
          </span>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          borderTop: "1px solid var(--rule)",
          borderBottom: "1px solid var(--rule)",
          paddingTop: 16,
          paddingBottom: 16,
        }}
      >
        {[
          { label: "Signals ready", value: data.signals_ready },
          { label: "Waiting", value: data.signals_waiting },
          { label: "Open positions", value: data.open_positions },
          { label: "Trades today", value: data.trades_today },
        ].map((s, i) => (
          <div
            key={s.label}
            style={{
              paddingLeft: i === 0 ? 0 : 16,
              borderLeft: i === 0 ? "none" : "1px solid var(--rule)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <span
              className="data-mono"
              style={{
                fontSize: 22,
                fontWeight: 600,
                color: "hsl(var(--fg))",
              }}
            >
              {fmtNumber(s.value)}
            </span>
            <span className="label-mono">{s.label}</span>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap" style={{ gap: 6 }}>
        {allClasses.map((ac) => {
          const active = data.asset_classes_enabled.includes(ac);
          return (
            <span
              key={ac}
              className="label-mono"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 8px",
                border: active
                  ? "1px solid var(--rule-brand)"
                  : "1px solid var(--rule)",
                color: active ? "hsl(var(--brand))" : "hsl(var(--fg-subtle))",
                background: "transparent",
                borderRadius: 2,
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 4,
                  height: 4,
                  background: active ? "hsl(var(--brand))" : "transparent",
                  border: active ? "none" : "1px solid hsl(var(--fg-subtle))",
                }}
              />
              {ac}
            </span>
          );
        })}
      </div>

      <p
        style={{
          fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
          fontSize: 11,
          color: "hsl(var(--fg-subtle))",
        }}
      >
        Last cycle: {data.last_pipeline_run ? fmtTime(data.last_pipeline_run) : "never"} ·{" "}
        {fmtNumber(data.signals_generated_last_cycle)} signals minted
      </p>
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
    value: string;
    hint?: string;
    status: "ok" | "warn" | "down";
  }[] = [
    {
      label: "API",
      value: "FastAPI · :8000",
      status: system?.api === "ok" ? "ok" : "down",
    },
    {
      label: "Paper runner",
      value: runner?.is_alive
        ? `live · ${runner?.mode ?? "—"}`
        : `idle · ${runner?.mode ?? "—"}`,
      hint:
        runner?.last_snapshot_age_seconds != null
          ? `last tick ${fmtAge(runner.last_snapshot_age_seconds)}`
          : undefined,
      status: runner?.is_alive ? "ok" : "warn",
    },
    {
      label: "Neo4j graph",
      value: neo4jLine,
      hint: system?.neo4j?.uri ? "Aura · neo4j+s://" : undefined,
      status: neo4jOk ? "ok" : "down",
    },
    {
      label: "LLM provider",
      value: llmLine,
      hint: system?.llm?.has_key ? "key wired" : "no key",
      status: llmOk ? "ok" : "warn",
    },
    {
      label: "Auto-execute",
      value: system?.config?.auto_execute ? "ON" : "off",
      hint: system?.config?.auto_execute ? "live orders" : "human in loop",
      status: system?.config?.auto_execute ? "ok" : "warn",
    },
  ];

  const dotColor = (s: "ok" | "warn" | "down") =>
    s === "ok"
      ? "hsl(var(--pos))"
      : s === "warn"
      ? "hsl(var(--warn))"
      : "hsl(var(--neg))";

  return (
    <ul style={{ listStyle: "none", padding: 0, margin: "24px 0 0" }}>
      {rows.map((r, i) => (
        <li
          key={r.label}
          className="flex items-center justify-between"
          style={{
            padding: "14px 0",
            borderTop: i === 0 ? "none" : "1px solid var(--rule)",
            gap: 16,
          }}
        >
          <span
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 13,
              color: "hsl(var(--fg-muted))",
            }}
          >
            {r.label}
          </span>
          <span
            className="flex items-center"
            style={{ gap: 10, textAlign: "right" }}
          >
            <span
              aria-hidden
              style={{
                width: 6,
                height: 6,
                borderRadius: 9999,
                background: dotColor(r.status),
                flexShrink: 0,
              }}
            />
            <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
              <span
                className="data-mono"
                style={{ fontSize: 13, color: "hsl(var(--fg))" }}
              >
                {r.value}
              </span>
              {r.hint && (
                <span
                  style={{
                    fontFamily:
                      'var(--font-sans), "Inter", system-ui, sans-serif',
                    fontSize: 11,
                    color: "hsl(var(--fg-subtle))",
                    marginTop: 2,
                  }}
                >
                  {r.hint}
                </span>
              )}
            </span>
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
    <div className="flex flex-col" style={{ gap: 24 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 16,
        }}
      >
        <BacktestCard
          title="Shock-driven · AYVID's mode"
          subtitle="Trade only inside graph-surfaced shock windows"
          report={shock ?? headline}
          accent="pos"
        />
        <BacktestCard
          title="Naive walk-forward · control"
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
  const better = (a: number | null | undefined, b: number | null | undefined, lowerWins: boolean) => {
    if (a == null || b == null) return null;
    if (a === b) return null;
    return lowerWins ? a < b : a > b;
  };
  return (
    <div style={{ overflowX: "auto", borderTop: "1px solid var(--rule-bold)" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          tableLayout: "fixed",
        }}
      >
        <thead>
          <tr style={{ borderBottom: "1px solid var(--rule-bold)" }}>
            <th
              className="label-mono"
              style={{ padding: "12px 16px", textAlign: "left", fontWeight: 500 }}
            >
              Mode
            </th>
            {rows.map((r) => (
              <th
                key={r.label}
                className="label-mono"
                style={{
                  padding: "12px 16px",
                  textAlign: "right",
                  fontWeight: 500,
                }}
              >
                {r.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[
            { side: "naive", report: naive, vs: shock, name: "Naive — trade every ticker daily" },
            { side: "shock", report: shock, vs: naive, name: "Shock-driven — AYVID's mode" },
          ].map((row) => (
            <tr
              key={row.side}
              style={{ borderBottom: "1px solid var(--rule)" }}
            >
              <td
                style={{
                  padding: "14px 16px",
                  fontFamily:
                    'var(--font-sans), "Inter", system-ui, sans-serif',
                  fontSize: 13,
                  color: "hsl(var(--fg))",
                }}
              >
                {row.name}
              </td>
              {rows.map((r) => {
                const v = row.report?.[r.key] as number | null | undefined;
                const o = row.vs?.[r.key] as number | null | undefined;
                const isWinner = better(v, o, !!r.lowerIsBetter);
                const color =
                  isWinner === true
                    ? "hsl(var(--pos))"
                    : isWinner === false
                    ? "hsl(var(--neg))"
                    : "hsl(var(--fg))";
                return (
                  <td
                    key={r.label}
                    className="data-mono"
                    style={{
                      padding: "14px 16px",
                      textAlign: "right",
                      fontSize: 13,
                      color,
                    }}
                  >
                    {fmtCell(v, r.isPct)}
                  </td>
                );
              })}
            </tr>
          ))}
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
      <div className="card-paper">
        <div className="label-mono">{title}</div>
        <div
          style={{
            fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
            fontSize: 11,
            color: "hsl(var(--fg-subtle))",
            marginTop: 6,
          }}
        >
          {subtitle}
        </div>
        <div
          style={{
            marginTop: 14,
            fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
            fontSize: 12,
            color: "hsl(var(--fg-subtle))",
          }}
        >
          No data
        </div>
      </div>
    );
  }
  const ret = report.compounded_return_pct ?? report.total_return_pct ?? 0;
  const cagr = report.cagr_pct;
  const positive = ret >= 0;
  return (
    <Link
      href={`/console/backtests/${report.name}`}
      className="card-paper block"
    >
      <div className="flex items-start justify-between" style={{ gap: 8 }}>
        <div>
          <div className="label-mono">{title}</div>
          <div
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 11,
              color: "hsl(var(--fg-subtle))",
              marginTop: 6,
            }}
          >
            {subtitle}
          </div>
        </div>
        <span
          className="label-mono"
          style={{
            padding: "3px 8px",
            border: `1px solid ${
              accent === "pos" ? "hsl(var(--pos) / 0.45)" : "hsl(var(--neg) / 0.45)"
            }`,
            color: accent === "pos" ? "hsl(var(--pos))" : "hsl(var(--neg))",
            borderRadius: 2,
          }}
        >
          {report.kind}
        </span>
      </div>
      <div className="flex items-baseline" style={{ marginTop: 18, gap: 12 }}>
        <span
          className="data-mono"
          style={{
            fontSize: 36,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: positive ? "hsl(var(--pos))" : "hsl(var(--neg))",
          }}
        >
          {fmtPct(ret)}
        </span>
        {cagr != null && (
          <span
            style={{
              fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
              fontSize: 11,
              color: "hsl(var(--fg-subtle))",
            }}
          >
            <span className="data-mono" style={{ color: "hsl(var(--fg))" }}>
              {fmtPct(cagr)}
            </span>{" "}
            CAGR
          </span>
        )}
      </div>
      <div
        style={{
          fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
          fontSize: 11,
          color: "hsl(var(--fg-subtle))",
          marginTop: 10,
        }}
      >
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
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {signals.map((s, idx) => {
        const long = s.direction === "LONG";
        return (
          <li
            key={`${s.ticker}-${idx}`}
            className="flex items-center justify-between"
            style={{
              padding: "12px 0",
              borderTop: idx === 0 ? "none" : "1px solid var(--rule)",
              gap: 16,
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div className="flex items-center" style={{ gap: 10 }}>
                <span
                  className="data-mono"
                  style={{ fontSize: 14, color: "hsl(var(--fg))" }}
                >
                  {s.ticker}
                </span>
                <span
                  className="label-mono"
                  style={{
                    color: long ? "hsl(var(--pos))" : "hsl(var(--neg))",
                  }}
                >
                  {long ? "▲ Long" : "▼ Short"}
                </span>
                <span
                  className="label-mono"
                  style={{ color: "hsl(var(--fg-subtle))" }}
                >
                  {s.instrument_type || "EQUITY"}
                </span>
              </div>
              {s.rationale && (
                <p
                  style={{
                    fontFamily:
                      'var(--font-sans), "Inter", system-ui, sans-serif',
                    fontSize: 11,
                    color: "hsl(var(--fg-subtle))",
                    marginTop: 6,
                    // Was nowrap-ellipsis — that fails to clip when
                    // the grid item itself has min-width: auto (CSS
                    // default), which lets the long unbroken
                    // rationale ("[SHOCK SAFETY HOLD: ...]") push the
                    // grid track wider than its fr-share and the page
                    // wider than the viewport.  Switching to a 2-line
                    // -webkit-line-clamp + overflow-wrap: anywhere
                    // means the text ALWAYS wraps inside its column
                    // even if there isn't a whitespace break-point
                    // for 200+ chars.  The conviction column on the
                    // right keeps its fixed cell, the layout stays
                    // intact, and the page no longer scrolls right.
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                    overflowWrap: "anywhere",
                    wordBreak: "break-word",
                    lineHeight: 1.5,
                  }}
                >
                  {s.rationale}
                </p>
              )}
            </div>
            <div className="text-right">
              <div
                className="data-mono"
                style={{ fontSize: 13, color: "hsl(var(--brand))" }}
              >
                {(s.conviction * 100).toFixed(0)}%
              </div>
              <div className="label-mono" style={{ marginTop: 2 }}>
                Conviction
              </div>
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
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {top.map((s, idx) => {
        const dir = s.expected_direction;
        const dirColor =
          dir === "BEARISH"
            ? "hsl(var(--neg))"
            : dir === "BULLISH"
            ? "hsl(var(--pos))"
            : "hsl(var(--fg-muted))";
        return (
          <li
            key={s.id}
            style={{
              borderTop: idx === 0 ? "none" : "1px solid var(--rule)",
            }}
          >
            <Link
              href={`/console/scenarios#${s.id}`}
              className="flex items-center justify-between"
              style={{ padding: "14px 0", gap: 16 }}
            >
              <div style={{ minWidth: 0 }}>
                <div
                  className="data-mono"
                  style={{
                    fontSize: 14,
                    color: "hsl(var(--fg))",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {s.name}
                </div>
                <div
                  style={{
                    fontFamily:
                      'var(--font-sans), "Inter", system-ui, sans-serif',
                    fontSize: 11,
                    color: "hsl(var(--fg-subtle))",
                    marginTop: 4,
                  }}
                >
                  {fmtDate(s.start_date)} · {s.tickers.length} tickers
                </div>
              </div>
              <span
                className="label-mono"
                style={{ color: dirColor }}
              >
                {dir ?? s.shock_type ?? "Shock"}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function QuickLinks({ headline }: { headline?: BacktestSummary }) {
  const links: { href: string; label: string; hint?: string }[] = [
    { href: "/console/live-shock", label: "Live shock demo", hint: "60-sec demo screen" },
    { href: "/console/portfolio", label: "Portfolio & positions" },
    { href: "/console/signals", label: "Signal book" },
    {
      href: "/console/backtests",
      label: "Backtest reports",
      hint: headline
        ? `latest: ${fmtPct(headline.compounded_return_pct ?? headline.total_return_pct ?? 0)}`
        : undefined,
    },
    { href: "/console/scenarios", label: "Shock catalog" },
    { href: "/console/portfolio", label: "Multi-asset allocation" },
  ];
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {links.map((l, idx) => (
        <li
          key={`${l.href}::${l.label}`}
          style={{
            borderTop: idx === 0 ? "none" : "1px solid var(--rule)",
          }}
        >
          <Link
            href={l.href}
            className="flex items-center justify-between"
            style={{ padding: "12px 0", gap: 16 }}
          >
            <span
              style={{
                fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
                fontSize: 13,
                color: "hsl(var(--fg))",
              }}
            >
              {l.label}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-sans), "Inter", system-ui, sans-serif',
                fontSize: 11,
                color: "hsl(var(--fg-subtle))",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              {l.hint ?? <ExternalLink className="h-3.5 w-3.5" />}
            </span>
          </Link>
        </li>
      ))}
      <li
        className="label-mono"
        style={{
          marginTop: 16,
          color: "hsl(var(--fg-subtle))",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Server className="h-3 w-3" />
        API · /api/v1/live/*
      </li>
    </ul>
  );
}

// Silence unused imports — kept for prop-shape compatibility if other
// files re-import Card from this module.
void Card;
void Briefcase;
void LineChart;
void Network;
void BarChart3;
void Zap;
