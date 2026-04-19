"use client";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquitySnapshot } from "@/lib/api";
import { fmtMoney, fmtTime, fmtDate } from "@/lib/format";

interface Props {
  snapshots: EquitySnapshot[];
  initial: number;
  height?: number;
}

export function EquityChart({ snapshots, initial, height = 280 }: Props) {
  if (!snapshots.length) {
    return (
      <div
        className="grid place-items-center text-xs text-muted"
        style={{ height }}
      >
        No live snapshots yet.
      </div>
    );
  }

  const data = snapshots.map((s) => ({
    t: new Date(s.timestamp).getTime(),
    equity: s.equity,
    pnl: s.equity - initial,
  }));

  const span = data[data.length - 1].t - data[0].t;
  const sameDay = span < 86_400_000;

  const last = data[data.length - 1].equity;
  const positive = last >= initial;
  const stroke = positive ? "hsl(var(--pos))" : "hsl(var(--neg))";

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 6" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => (sameDay ? fmtTime(new Date(v).toISOString()) : fmtDate(new Date(v).toISOString()))}
            tick={{ fill: "hsl(var(--fg-muted))", fontSize: 11 }}
            axisLine={{ stroke: "hsl(var(--border))" }}
            tickLine={false}
            minTickGap={64}
          />
          <YAxis
            dataKey="equity"
            domain={["auto", "auto"]}
            tickFormatter={(v) => fmtMoney(v, { compact: true, decimals: 0 })}
            tick={{ fill: "hsl(var(--fg-muted))", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={64}
          />
          <Tooltip
            cursor={{ stroke: "hsl(var(--border-strong))", strokeWidth: 1 }}
            contentStyle={{
              background: "hsl(var(--panel))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 10,
              fontSize: 12,
              padding: "10px 12px",
              boxShadow: "0 6px 18px hsl(var(--shadow) / 0.18)",
            }}
            labelStyle={{ color: "hsl(var(--fg-muted))", fontSize: 11, marginBottom: 4 }}
            formatter={(value: number, name: string) => {
              if (name === "equity") return [fmtMoney(value), "Equity"];
              return [value, name];
            }}
            labelFormatter={(v: number) =>
              new Date(v).toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })
            }
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={stroke}
            strokeWidth={2}
            fill="url(#equityFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
