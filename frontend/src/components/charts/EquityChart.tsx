"use client";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useMemo } from "react";
import type { EquitySnapshot } from "@/lib/api";
import {
  computeChartDomain,
  fmtChartAxis,
  fmtMoney,
} from "@/lib/format";

interface Props {
  snapshots: EquitySnapshot[];
  initial: number;
  height?: number;
  showGrid?: boolean;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: ReadonlyArray<{ value?: number | string | Array<number | string> | undefined }>;
  label?: string | number;
  initial: number;
}

function ChartTooltip({ active, payload, label, initial }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const raw = payload[0]?.value;
  const equity = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(equity)) return null;
  const pnl = equity - initial;
  const pnlPct = initial > 0 ? ((equity - initial) / initial) * 100 : 0;
  const positive = pnl >= 0;

  return (
    <div
      style={{
        background: "hsl(var(--panel))",
        border: "1px solid hsl(var(--border))",
        borderRadius: 10,
        padding: "10px 14px",
        boxShadow: "0 8px 28px hsl(var(--shadow) / 0.32)",
        minWidth: 200,
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontFamily: "var(--font-mono, monospace)",
          color: "hsl(var(--fg-muted))",
          letterSpacing: "0.06em",
          marginBottom: 8,
        }}
      >
        {label
          ? new Date(label).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "—"}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 24 }}>
          <span style={{ fontSize: 11, color: "hsl(var(--fg-muted))" }}>Portfolio</span>
          <span style={{ fontSize: 13, fontWeight: 600, color: "hsl(var(--fg))" }}>
            {fmtMoney(equity)}
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 24 }}>
          <span style={{ fontSize: 11, color: "hsl(var(--fg-muted))" }}>P&L</span>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: positive ? "hsl(var(--pos))" : "hsl(var(--neg))",
            }}
          >
            {positive ? "+" : ""}
            {fmtMoney(pnl)}
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 24 }}>
          <span style={{ fontSize: 11, color: "hsl(var(--fg-muted))" }}>Return</span>
          <span
            style={{
              fontSize: 11,
              color: positive ? "hsl(var(--pos))" : "hsl(var(--neg))",
            }}
          >
            {positive ? "+" : ""}
            {pnlPct.toFixed(3)}%
          </span>
        </div>
      </div>
    </div>
  );
}

export function EquityChart({ snapshots, initial, height = 300, showGrid = true }: Props) {
  const data = useMemo(
    () =>
      snapshots.map((s) => ({
        t: new Date(s.timestamp).getTime(),
        equity: s.equity,
      })),
    [snapshots],
  );

  const equityValues = useMemo(() => data.map((d) => d.equity), [data]);

  // Adaptive domain — never flat
  const [domainMin, domainMax] = useMemo(
    () => computeChartDomain(equityValues, 0.005),
    [equityValues],
  );
  const range = domainMax - domainMin;

  // Split fill + stroke at the inception line so the chart visually
  // distinguishes "above water" (green) from "below water" (red) the
  // way a fund tearsheet does. Without this a portfolio that ran +1.4%
  // peak then dipped to -0.002% reads as a fully red disaster.
  const inceptionOffsetPct =
    range > 0
      ? Math.max(0, Math.min(100, ((domainMax - initial) / range) * 100))
      : 50;

  const timeSpanMs = data.length > 1 ? data[data.length - 1].t - data[0].t : 0;
  const xFormatter = (ts: number) => {
    const d = new Date(ts);
    if (timeSpanMs < 86_400_000) {
      return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    }
    if (timeSpanMs < 7 * 86_400_000) {
      return (
        d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
        " " +
        d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
      );
    }
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  if (!data.length) {
    return (
      <div
        className="grid place-items-center text-xs text-muted"
        style={{
          height,
          fontFamily: "var(--font-mono, monospace)",
          letterSpacing: "0.08em",
        }}
        aria-label="fund performance chart awaiting snapshot"
      >
        AWAITING FIRST SNAPSHOT
      </div>
    );
  }

  return (
    <div
      style={{ width: "100%", height }}
      role="img"
      aria-label="fund performance chart"
    >
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="fund-fill-split" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(var(--pos))" stopOpacity={0.28} />
              <stop offset={`${inceptionOffsetPct}%`} stopColor="hsl(var(--pos))" stopOpacity={0.04} />
              <stop offset={`${inceptionOffsetPct}%`} stopColor="hsl(var(--neg))" stopOpacity={0.04} />
              <stop offset="100%" stopColor="hsl(var(--neg))" stopOpacity={0.28} />
            </linearGradient>
            <linearGradient id="fund-line-split" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(var(--pos))" />
              <stop offset={`${inceptionOffsetPct}%`} stopColor="hsl(var(--pos))" />
              <stop offset={`${inceptionOffsetPct}%`} stopColor="hsl(var(--neg))" />
              <stop offset="100%" stopColor="hsl(var(--neg))" />
            </linearGradient>
          </defs>

          {showGrid && (
            <CartesianGrid
              strokeDasharray="2 4"
              stroke="hsl(var(--border))"
              vertical={false}
            />
          )}

          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={xFormatter}
            tick={{ fill: "hsl(var(--fg-muted))", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickCount={6}
            minTickGap={60}
          />

          <YAxis
            dataKey="equity"
            domain={[domainMin, domainMax]}
            tickFormatter={(v) => fmtChartAxis(v, range)}
            tick={{ fill: "hsl(var(--fg-muted))", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={72}
            tickCount={5}
          />

          <ReferenceLine
            y={initial}
            stroke="hsl(var(--border-strong))"
            strokeDasharray="3 4"
            strokeWidth={1}
          />

          <Tooltip
            content={(props) => <ChartTooltip {...props} initial={initial} />}
            cursor={{
              stroke: "hsl(var(--border-strong))",
              strokeWidth: 1,
              strokeDasharray: "3 3",
            }}
          />

          <Area
            type="monotone"
            dataKey="equity"
            stroke="url(#fund-line-split)"
            strokeWidth={1.6}
            fill="url(#fund-fill-split)"
            dot={false}
            activeDot={{
              r: 4,
              fill:
                (equityValues[equityValues.length - 1] ?? initial) >= initial
                  ? "hsl(var(--pos))"
                  : "hsl(var(--neg))",
              stroke: "hsl(var(--bg))",
              strokeWidth: 2,
            }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
