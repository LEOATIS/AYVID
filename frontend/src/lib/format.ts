/** Lightweight formatting helpers — money, percent, time, compact numbers. */

export const fmtMoney = (n: number | null | undefined, opts: { compact?: boolean; decimals?: number } = {}) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (opts.compact && Math.abs(n) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", notation: "compact",
      maximumFractionDigits: 1,
    }).format(n);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: opts.decimals ?? 2,
    maximumFractionDigits: opts.decimals ?? 2,
  }).format(n);
};

export const fmtPct = (n: number | null | undefined, decimals = 2) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(decimals)}%`;
};

export const fmtNumber = (n: number | null | undefined, decimals = 0) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }).format(n);
};

export const fmtCompact = (n: number | null | undefined) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
};

/**
 * Picks between full ($1,234,567) and compact ($1.2M) currency formatting
 * based on the absolute magnitude of the number.  Use this anywhere a
 * dollar value has to fit inside a narrow tile.
 *
 *   < 10k        → "$8,420"
 *   10k → 999k   → "$842K"
 *   1M → 999M    → "$1.2M"
 *   ≥ 1B         → "$1.2B"
 */
export const fmtMoneyAdaptive = (n: number | null | undefined) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs < 10_000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(n);
  }
  // Two-decimal compact so values within rounding distance of a round
  // million ($1,014,024 vs $999,979) don't both collapse to "$1M". The
  // boundary between K and M lands at $999.5K so $999,979 reads "$1.00M"
  // instead of the misleading "$1,000K".
  if (abs < 999_500) {
    return `$${Math.round(n / 1_000).toLocaleString("en-US")}K`;
  }
  if (abs < 1_000_000_000) {
    return `$${(n / 1_000_000).toFixed(2)}M`;
  }
  return `$${(n / 1_000_000_000).toFixed(2)}B`;
};

export const fmtTime = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export const fmtDate = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

export const fmtDateTime = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const fmtAge = (seconds?: number | null) => {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
};

export const fmtUptime = (seconds?: number | null) => {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
};

/**
 * Range-aware Y-axis label for portfolio charts.
 *
 * The default `fmtMoney(v, {compact: true, decimals: 0})` rounds
 * every value within $500K of $1M to "$1M", producing a flat-looking
 * axis. This function picks precision based on the visible range so
 * sub-1% moves on a $1M portfolio still produce distinct labels.
 *
 * Range  <   $5K   → "$1,002.8K"
 * Range  <  $50K   → "$1,003K"
 * Range  < $500K   → "$1.003M"
 * Range  ≥ $500K   → "$1.5M"
 */
export const fmtChartAxis = (value: number, range: number): string => {
  const r = Math.abs(range);
  if (r < 5_000) {
    return `$${(value / 1_000).toFixed(1)}K`;
  }
  if (r < 50_000) {
    return `$${Math.round(value / 1_000).toLocaleString()}K`;
  }
  if (r < 500_000) {
    return `$${(value / 1_000_000).toFixed(3)}M`;
  }
  return `$${(value / 1_000_000).toFixed(1)}M`;
};

/**
 * Adaptive [min, max] domain for a portfolio Y-axis.
 *
 * Enforces a minimum visible range (default 0.5% of mid) so a
 * portfolio that's only moved $200 over a session still produces a
 * non-flat chart. Adds 15% padding on each side so the line never
 * touches the chart edges.
 */
export const computeChartDomain = (
  values: number[],
  minRangePct: number = 0.005,
): [number, number] => {
  if (!values.length) return [0, 1_000_000];
  let lo = values[0];
  let hi = values[0];
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const mid = (lo + hi) / 2;
  const range = hi - lo;
  const minRange = Math.abs(mid) * minRangePct;
  const effective = Math.max(range, minRange);
  const padding = effective * 0.15;
  return [
    Math.floor(mid - effective / 2 - padding),
    Math.ceil(mid + effective / 2 + padding),
  ];
};
