"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LineChart } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { useSignals, useSignalsHistory } from "@/lib/hooks";
import type {
  ReadinessStatus,
  Signal,
  SignalReadinessEvent,
  SignalHistoryRecord,
} from "@/lib/api";
import { fmtAge, fmtMoney, fmtNumber } from "@/lib/format";

// ── Asset-class palette ────────────────────────────────────────────
const ASSET_COLOR: Record<string, string> = {
  EQUITY: "#3B82F6",
  INDEX: "#8B5CF6",
  FX: "#22C55E",
  FUTURE: "#F59E0B",
  CRYPTO: "#F97316",
  ETF: "#06B6D4",
  OPTION: "#EC4899",
  COMMODITY: "#EAB308",
  BOND: "#14B8A6",
};

const ASSET_FILTERS = [
  "ALL",
  "EQUITY",
  "INDEX",
  "FX",
  "FUTURE",
  "CRYPTO",
  "ETF",
  "OPTION",
  "COMMODITY",
] as const;

type AssetFilter = (typeof ASSET_FILTERS)[number];
type StatusFilter = "ALL" | ReadinessStatus;

// ── Readiness badge ────────────────────────────────────────────────
function ReadinessBadge({ status }: { status: string | undefined | null }) {
  const s = (status || "WAITING").toUpperCase();
  if (s === "READY") return <Badge variant="pos">● READY</Badge>;
  if (s === "EXPIRED") return <Badge variant="neutral">EXPIRED</Badge>;
  if (s === "CANCELLED") return <Badge variant="neg">CANCELLED</Badge>;
  return <Badge variant="warn">◌ WAITING</Badge>;
}

// ── Distance-to-entry indicator ────────────────────────────────────
function DistanceIndicator({
  pct,
  direction,
}: {
  pct: number | null | undefined;
  direction: string;
}) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) {
    return <span className="text-muted">—</span>;
  }
  const isLong = direction === "LONG";
  const absPct = Math.abs(pct) * 100;
  // For LONG: negative pct = current below entry = approaching from below.
  // For SHORT: positive pct = current above entry = approaching from above.
  const isApproaching = isLong ? pct <= 0 : pct >= 0;
  const color = absPct < 0.3 ? "var(--pos)" : absPct < 1.0 ? "var(--warn)" : "var(--muted)";
  const label = absPct < 0.3 ? "AT ENTRY" : isApproaching ? "APPROACHING" : "AWAY";

  return (
    <div className="flex flex-col gap-0.5">
      <span className="num text-[12px] font-semibold" style={{ color }}>
        {pct > 0 ? "+" : ""}
        {(pct * 100).toFixed(2)}%
      </span>
      <div className="h-[3px] w-[60px] rounded-full bg-[hsl(var(--bg-sunken))]">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{
            width: `${Math.min((absPct / 2) * 100, 100)}%`,
            background: color,
          }}
        />
      </div>
      <span className="num text-[9px] text-muted">{label}</span>
    </div>
  );
}

// ── SL / TP visual bar ─────────────────────────────────────────────
function SlTpBar({ signal }: { signal: Signal }) {
  const cp = signal.current_price ?? 0;
  const ep = signal.entry_price ?? 0;
  const sl = signal.stop_loss_price ?? 0;
  const tp = signal.take_profit_price ?? 0;
  if (!cp || !ep || !sl || !tp) return null;

  const lo = Math.min(sl, tp);
  const hi = Math.max(sl, tp);
  const range = hi - lo;
  if (range <= 0) return null;
  const entryPct = ((ep - lo) / range) * 100;
  const currentPct = ((cp - lo) / range) * 100;
  const clamp = (v: number) => Math.max(0, Math.min(100, v));
  const isLong = signal.direction === "LONG";

  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-1.5 w-[160px] rounded-full bg-[hsl(var(--bg-sunken))]">
        <div
          className="absolute left-0 h-full rounded-l-full"
          style={{
            width: `${entryPct}%`,
            background: isLong ? "hsl(var(--neg) / 0.25)" : "hsl(var(--pos) / 0.20)",
          }}
        />
        <div
          className="absolute h-full rounded-r-full"
          style={{
            left: `${entryPct}%`,
            width: `${100 - entryPct}%`,
            background: isLong ? "hsl(var(--pos) / 0.20)" : "hsl(var(--neg) / 0.25)",
          }}
        />
        {/* Entry tick */}
        <div
          className="absolute -top-0.5 h-[10px] w-[2px] rounded-full bg-brand"
          style={{ left: `${clamp(entryPct)}%`, transform: "translateX(-50%)" }}
        />
        {/* Current price marker */}
        <div
          className="absolute -top-1 h-3 w-[3px] rounded-full bg-fg transition-[left] duration-500"
          style={{ left: `${clamp(currentPct)}%`, transform: "translateX(-50%)" }}
        />
      </div>
      <div className="flex w-[160px] justify-between text-[9px] text-muted">
        <span className="num text-neg">SL {fmtMoney(sl)}</span>
        <span className="num text-pos">TP {fmtMoney(tp)}</span>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────
// Cast a single SignalHistoryRecord into the Signal shape the rest of
// this page renders. History rows can be missing some fields (especially
// the inferred PORTFOLIO markers), so we fill in safe defaults.
function _historyRecordToSignal(r: SignalHistoryRecord): Signal {
  return {
    ticker: r.ticker ?? "?",
    direction: (r.direction ?? "LONG") as Signal["direction"],
    instrument_type: r.instrument_type ?? r.asset_class ?? "EQUITY",
    asset_class: (r.asset_class ?? "EQUITY") as Signal["asset_class"],
    conviction: r.conviction ?? 0,
    strategy_name: r.strategy_name ?? r.event ?? "",
    strike: r.strike ?? null,
    expiry_date: r.expiry_date ?? "",
    stop_loss_pct: r.stop_loss_pct ?? 0,
    take_profit_pct: r.take_profit_pct ?? 0,
    rationale: r.rationale ?? "",
    computed_at: r.computed_at ?? r.logged_at,
    ttl_minutes: r.ttl_minutes ?? 30,
    regime_fingerprint: r.regime_fingerprint ?? "",
    expected_r: r.expected_r ?? 0,
    min_r: r.min_r ?? 0,
    entry_price: r.entry_price ?? r.entry_price_at_compute ?? 0,
    entry_price_at_compute: r.entry_price_at_compute ?? r.entry_price ?? 0,
    entry_zone_low: r.entry_zone_low ?? 0,
    entry_zone_high: r.entry_zone_high ?? 0,
    stop_loss_price: r.stop_loss_price ?? 0,
    take_profit_price: r.take_profit_price ?? 0,
    risk_reward_ratio: r.risk_reward_ratio ?? 0,
    quantity: r.quantity ?? 0,
    notional_value: r.notional_value ?? 0,
    risk_amount: r.risk_amount ?? 0,
    kelly_fraction: r.kelly_fraction ?? 0,
    contract_multiplier: r.contract_multiplier ?? null,
    pip_value: r.pip_value ?? null,
    margin_required: r.margin_required ?? null,
    factor_score: r.factor_score ?? 0,
    expires_at: r.expires_at ?? r.logged_at,
    readiness_status: (r.readiness_status ?? "EXPIRED") as ReadinessStatus,
    readiness_reason: r.readiness_reason ?? r.event ?? "",
    readiness_checked_at: r.readiness_checked_at ?? r.logged_at,
    current_price: r.current_price ?? null,
    distance_to_entry_pct: r.distance_to_entry_pct ?? null,
  } as Signal;
}

const _today = () => new Date().toISOString().slice(0, 10);
const _daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

export default function SignalsPage() {
  const [view, setView] = useState<"active" | "history">("active");
  const [dateStart, setDateStart] = useState<string>(_daysAgo(14));
  const [dateEnd, setDateEnd] = useState<string>(_today());

  const { data: activeData, error: activeError, isLoading: activeLoading } =
    useSignals();
  const {
    data: historyData,
    error: historyError,
    isLoading: historyLoading,
  } = useSignalsHistory(
    view === "history"
      ? {
          start: `${dateStart}T00:00:00`,
          end: `${dateEnd}T23:59:59`,
          limit: 2000,
        }
      : {},
  );

  const isLoading = view === "active" ? activeLoading : historyLoading;
  const error = view === "active" ? activeError : historyError;
  const data = view === "active" ? activeData : undefined;

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("ALL");
  const [assetFilter, setAssetFilter] = useState<AssetFilter>("ALL");
  const [flashTickers, setFlashTickers] = useState<Set<string>>(new Set());
  const eventSourceRef = useRef<EventSource | null>(null);

  // Subscribe to SSE for real-time readiness changes — flash any
  // ticker that just promoted to READY.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const apiBase = (process.env.NEXT_PUBLIC_API_BASE || "/api/v1").replace(/\/$/, "");
    const url = `${apiBase}/live/signals/stream`;
    let es: EventSource;
    try {
      es = new EventSource(url);
    } catch {
      return;
    }
    eventSourceRef.current = es;
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data) as SignalReadinessEvent;
        if (
          evt.type === "readiness_change" &&
          evt.readiness_status === "READY" &&
          evt.ticker
        ) {
          const t = evt.ticker;
          setFlashTickers((prev) => {
            const next = new Set(prev);
            next.add(t);
            return next;
          });
          window.setTimeout(() => {
            setFlashTickers((prev) => {
              const next = new Set(prev);
              next.delete(t);
              return next;
            });
          }, 3000);
        }
      } catch {
        // ignore malformed events
      }
    };
    es.onerror = () => {
      // Browser will auto-reconnect; nothing to do here.
    };
    return () => {
      try {
        es.close();
      } catch {
        // already closed
      }
    };
  }, []);

  const signals: Signal[] =
    view === "active"
      ? activeData?.signals ?? []
      : (historyData?.records ?? [])
          // Skip the synthetic PORTFOLIO markers that have no ticker —
          // they're useful for the timeline summary but don't render
          // as signal cards.
          .filter((r) => r.ticker && r.ticker !== "PORTFOLIO")
          .map(_historyRecordToSignal);

  const filtered = useMemo(() => {
    return signals.filter((s) => {
      if (statusFilter !== "ALL" && (s.readiness_status || "WAITING") !== statusFilter) {
        return false;
      }
      if (assetFilter !== "ALL" && s.asset_class !== assetFilter) return false;
      return true;
    });
  }, [signals, statusFilter, assetFilter]);

  // Sort: READY first, then highest conviction.
  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const aReady = a.readiness_status === "READY" ? 0 : 1;
      const bReady = b.readiness_status === "READY" ? 0 : 1;
      if (aReady !== bReady) return aReady - bReady;
      return (b.conviction ?? 0) - (a.conviction ?? 0);
    });
  }, [filtered]);

  // Counts come from the active SignalsResponse in active view, and
  // from the history endpoint's by_status aggregate in history view.
  const histCounts = historyData?.by_status ?? {};
  const ready =
    view === "active"
      ? activeData?.ready_count ?? 0
      : histCounts.READY ?? 0;
  const waiting =
    view === "active"
      ? activeData?.waiting_count ?? 0
      : histCounts.WAITING ?? 0;
  const expired =
    view === "active"
      ? activeData?.expired_count ?? 0
      : histCounts.EXPIRED ?? 0;
  const cancelled =
    view === "active"
      ? activeData?.cancelled_count ?? 0
      : histCounts.CANCELLED ?? 0;
  const filled = view === "history" ? histCounts.FILLED ?? 0 : 0;
  const total =
    view === "active"
      ? activeData?.total ?? signals.length
      : historyData?.total ?? signals.length;

  const filterPills: { label: StatusFilter; count: number }[] = [
    { label: "ALL", count: total },
    { label: "READY", count: ready },
    { label: "WAITING", count: waiting },
    { label: "EXPIRED", count: expired },
    { label: "CANCELLED", count: cancelled },
  ];

  return (
    <div className="flex flex-col gap-6 fade-up">
      {/* View toggle + date range — sits above the existing header */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-full bg-[hsl(var(--bg-sunken))] p-0.5">
          <button
            type="button"
            onClick={() => setView("active")}
            className={[
              "px-4 py-1.5 text-[11px] font-mono font-medium tracking-[0.06em] rounded-full transition-colors",
              view === "active"
                ? "bg-brand text-bg"
                : "text-muted hover:text-fg",
            ].join(" ")}
          >
            ◌ ACTIVE BOOK
          </button>
          <button
            type="button"
            onClick={() => setView("history")}
            className={[
              "px-4 py-1.5 text-[11px] font-mono font-medium tracking-[0.06em] rounded-full transition-colors",
              view === "history"
                ? "bg-brand text-bg"
                : "text-muted hover:text-fg",
            ].join(" ")}
          >
            ⏱ FULL HISTORY
          </button>
        </div>
        {view === "history" && (
          <>
            <input
              type="date"
              value={dateStart}
              onChange={(e) => setDateStart(e.target.value)}
              max={dateEnd}
              className="num rounded-md bg-[hsl(var(--bg-sunken))] px-3 py-1.5 text-[12px] text-fg ring-1 ring-inset ring-[hsl(var(--border))] focus:outline-none focus:ring-brand"
            />
            <span className="text-[12px] text-subtle">→</span>
            <input
              type="date"
              value={dateEnd}
              onChange={(e) => setDateEnd(e.target.value)}
              min={dateStart}
              max={_today()}
              className="num rounded-md bg-[hsl(var(--bg-sunken))] px-3 py-1.5 text-[12px] text-fg ring-1 ring-inset ring-[hsl(var(--border))] focus:outline-none focus:ring-brand"
            />
            <span className="num text-[10px] tracking-wider text-subtle">
              {historyData?.total ?? 0} records
              {historyData?.earliest && historyData.latest && (
                <>
                  {" · "}
                  {historyData.earliest.slice(0, 16)} → {historyData.latest.slice(0, 16)}
                </>
              )}
              {filled > 0 && <> · <span className="text-pos">{filled} FILLED</span></>}
            </span>
          </>
        )}
      </div>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight text-fg">Signals</h1>
          <p className="mt-1 text-[13px] text-muted">
            <span className="num font-semibold text-pos">{ready}</span> ready ·{" "}
            <span className="num font-semibold text-warn">{waiting}</span> waiting · updated{" "}
            {data?.snapshot_at
              ? fmtAge((Date.now() - new Date(data.snapshot_at).getTime()) / 1000)
              : "—"}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          {filterPills.map((f) => (
            <button
              key={f.label}
              onClick={() => setStatusFilter(f.label)}
              className={[
                "px-3 py-1 text-[11px] font-medium tracking-wider rounded-full",
                "ring-1 ring-inset transition-colors",
                statusFilter === f.label
                  ? "bg-brand text-bg ring-brand"
                  : "bg-[hsl(var(--bg-sunken))] text-muted ring-[hsl(var(--border))] hover:text-fg",
              ].join(" ")}
              type="button"
            >
              {f.label}
              {f.count > 0 && <span className="num ml-1.5 opacity-70">({f.count})</span>}
            </button>
          ))}
        </div>
      </header>

      <div className="-mt-2 flex flex-wrap gap-1">
        {ASSET_FILTERS.map((ac) => {
          const active = assetFilter === ac;
          const color = ac === "ALL" ? undefined : ASSET_COLOR[ac];
          return (
            <button
              key={ac}
              onClick={() => setAssetFilter(ac)}
              className={[
                "px-2.5 py-1 text-[10px] font-medium tracking-wider rounded-full transition-colors",
                "ring-1 ring-inset",
                active
                  ? "bg-[hsl(var(--bg-sunken))] text-fg"
                  : "bg-transparent text-muted hover:text-fg",
              ].join(" ")}
              style={
                active && color
                  ? {
                      color,
                      borderColor: color,
                      background: `${color}15`,
                      boxShadow: `inset 0 0 0 1px ${color}50`,
                    }
                  : undefined
              }
              type="button"
            >
              {ac}
            </button>
          );
        })}
      </div>

      <Card
        title={`Signal book · ${sorted.length} match${sorted.length === 1 ? "" : "es"}`}
        subtitle="Live SL/TP, sizing, and trade-readiness — refresh every 3s, real-time SSE flash on READY"
      >
        {isLoading && <div className="skeleton h-32" />}
        {error && (
          <Empty
            icon={<LineChart className="h-8 w-8" />}
            title="Failed to load signal book"
            hint={String(error)}
          />
        )}
        {!isLoading && !error && sorted.length === 0 && (
          <Empty
            icon={<LineChart className="h-8 w-8" />}
            title="No signals match the current filter"
            hint="Run `ayvid trade signals` from the CLI to populate, or wait for the next pipeline cycle."
          />
        )}

        {sorted.length > 0 && (
          <div className="flex flex-col gap-2">
            {sorted.map((s, idx) => {
              const ac = (s.asset_class || "EQUITY") as string;
              const color = ASSET_COLOR[ac] ?? "#64748B";
              const isReady = s.readiness_status === "READY";
              const isFlashing = s.ticker ? flashTickers.has(s.ticker) : false;
              const isLong = s.direction === "LONG";
              const isHold = s.direction === "HOLD";

              const conv = (s.conviction ?? 0) * 100;
              const qty = s.quantity ?? 0;
              const qtyUnit =
                ac === "FX"
                  ? "lots"
                  : ac === "FUTURE" || ac === "COMMODITY" || ac === "OPTION"
                  ? "contracts"
                  : ac === "CRYPTO"
                  ? "units"
                  : "shares";

              return (
                <div
                  key={`${s.ticker}-${s.regime_fingerprint ?? "norfp"}-${idx}`}
                  className={[
                    "rounded-xl border px-5 py-4 transition-all duration-300",
                    isReady
                      ? "border-pos/30 bg-gradient-to-br from-pos/5 to-transparent"
                      : "border-app bg-[hsl(var(--bg))]",
                    isFlashing ? "animate-flash-ready" : "",
                  ].join(" ")}
                >
                  {/* Row 1: identity + readiness */}
                  <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                    <div className="flex items-center gap-3">
                      <span
                        className="num inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider ring-1 ring-inset"
                        style={{
                          color,
                          borderColor: `${color}50`,
                          background: `${color}15`,
                          boxShadow: `inset 0 0 0 1px ${color}40`,
                        }}
                      >
                        {ac}
                      </span>
                      <span className="text-[18px] font-semibold tracking-tight text-fg">
                        {s.ticker}
                      </span>
                      <Badge variant={isLong ? "pos" : isHold ? "neutral" : "neg"}>
                        {isLong ? "▲ LONG" : isHold ? "◇ HOLD" : "▼ SHORT"}
                      </Badge>
                      <span className="num text-[11px] text-muted">{s.strategy_name}</span>
                    </div>

                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-1.5">
                        <span className="num text-[10px] tracking-wider text-muted">
                          CONVICTION
                        </span>
                        <div className="h-1 w-12 rounded-full bg-[hsl(var(--bg-sunken))]">
                          <div
                            className="h-full rounded-full bg-brand transition-[width] duration-500"
                            style={{ width: `${conv}%` }}
                          />
                        </div>
                        <span className="num text-[11px] text-brand">
                          {conv.toFixed(0)}%
                        </span>
                      </div>
                      <ReadinessBadge status={s.readiness_status} />
                    </div>
                  </div>

                  {/* Row 2: numbers grid */}
                  <div className="grid gap-4 mb-3 grid-cols-[repeat(auto-fit,minmax(110px,1fr))]">
                    <Tile label="CURRENT" value={s.current_price ? fmtMoney(s.current_price) : "—"} />
                    <Tile
                      label="ENTRY"
                      value={s.entry_price ? fmtMoney(s.entry_price) : "—"}
                      valueClass="text-brand"
                    />
                    <Tile
                      label="STOP LOSS"
                      value={s.stop_loss_price ? fmtMoney(s.stop_loss_price) : "—"}
                      sub={
                        s.stop_loss_pct ? `−${fmtNumber(s.stop_loss_pct, 2)}%` : null
                      }
                      valueClass="text-neg"
                      subClass="text-neg/80"
                    />
                    <Tile
                      label="TARGET"
                      value={s.take_profit_price ? fmtMoney(s.take_profit_price) : "—"}
                      sub={
                        s.take_profit_pct ? `+${fmtNumber(s.take_profit_pct, 2)}%` : null
                      }
                      valueClass="text-pos"
                      subClass="text-pos/80"
                    />
                    <Tile
                      label="QTY"
                      value={qty ? fmtNumber(qty, qty < 1 ? 4 : 0) : "—"}
                      sub={qty ? qtyUnit : null}
                    />
                    <Tile
                      label="R:R"
                      value={
                        s.risk_reward_ratio
                          ? `1:${(s.risk_reward_ratio ?? 0).toFixed(1)}`
                          : "—"
                      }
                      sub={
                        s.risk_amount ? `risk ${fmtMoney(s.risk_amount)}` : null
                      }
                    />
                    <div className="flex flex-col gap-1">
                      <span className="num text-[10px] tracking-wider text-muted">
                        DISTANCE
                      </span>
                      <DistanceIndicator
                        pct={s.distance_to_entry_pct}
                        direction={String(s.direction)}
                      />
                    </div>
                  </div>

                  {/* Row 3: SL/TP bar + rationale + status footer */}
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <SlTpBar signal={s} />

                    <p className="min-w-[200px] flex-1 text-[12px] italic text-muted leading-relaxed">
                      {s.rationale ? `"${s.rationale}"` : ""}
                    </p>

                    <div className="flex flex-col items-end gap-0.5">
                      <span className="num text-[10px] text-muted">
                        {s.expires_at
                          ? `expires ${fmtAge((new Date(s.expires_at).getTime() - Date.now()) / 1000)}`
                          : ""}
                      </span>
                      {s.readiness_status === "WAITING" && s.readiness_reason && (
                        <span className="num text-[10px] text-warn">
                          ◌ {s.readiness_reason}
                        </span>
                      )}
                      {s.readiness_status === "READY" && (
                        <span className="num text-[10px] font-semibold text-pos">
                          ● ROUTING TO BROKER
                        </span>
                      )}
                      {s.notional_value !== null && s.notional_value !== undefined && s.notional_value > 0 && (
                        <span className="num text-[10px] text-muted">
                          notional {fmtMoney(s.notional_value)}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <style jsx global>{`
        @keyframes flash-ready {
          0%,
          100% {
            border-color: hsl(var(--pos) / 0.3);
            box-shadow: 0 0 0 rgba(34, 197, 94, 0);
          }
          50% {
            border-color: hsl(var(--pos) / 0.7);
            box-shadow: 0 0 24px rgba(34, 197, 94, 0.18);
          }
        }
        .animate-flash-ready {
          animation: flash-ready 0.6s ease-in-out 3;
        }
      `}</style>
    </div>
  );
}

function Tile({
  label,
  value,
  sub,
  valueClass,
  subClass,
}: {
  label: string;
  value: string | number;
  sub?: string | null;
  valueClass?: string;
  subClass?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="num text-[10px] tracking-wider text-muted">{label}</span>
      <span
        className={["num text-[16px] font-semibold tracking-tight", valueClass ?? "text-fg"].join(" ")}
      >
        {value}
      </span>
      {sub && <span className={["num text-[10px]", subClass ?? "text-muted"].join(" ")}>{sub}</span>}
    </div>
  );
}

