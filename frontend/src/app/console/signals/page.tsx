"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LineChart } from "lucide-react";
import { Empty } from "@/components/ui/Empty";
import { useSignals, useSignalsHistory } from "@/lib/hooks";
import type {
  ReadinessStatus,
  Signal,
  SignalReadinessEvent,
  SignalHistoryRecord,
} from "@/lib/api";
import { fmtAge, fmtMoney, fmtNumber } from "@/lib/format";

/**
 * AYVID signals — specimen cards on warm paper, per brief §5.4.
 *
 * Each signal is treated like an entry in a museum catalogue:
 *  • Card surface: var(--bg-1) on a 1px var(--rule), 6px radius, no shadow.
 *  • Strict 12-column grid:
 *      ├─ col 1-2  asset tag · ticker (Fraunces 24px) · direction · strategy
 *      ├─ col 3-9  CURRENT / ENTRY / SL / TARGET / QTY / R:R as small
 *      │           numeric blocks — .label-mono on top, .data-mono large below
 *      └─ col 10-12  conviction bar · status pill · expiry
 *  • SL/TP bar: 2px thin horizontal line with tick markers (was a thick
 *    coloured bar).
 *  • READY status: small filled gold square (4×4px) + "ROUTING"
 *  • WAITING: empty 1px-border gold square + "AWAITING ENTRY"
 *  • EXPIRED: muted text, no square, just "WINDOW CLOSED"
 *
 * Every existing hook (useSignals, useSignalsHistory, SSE for READY
 * flash) is preserved verbatim.  Only the visual surface changes.
 */

// ── Asset-class palette ────────────────────────────────────────────
// Slightly desaturated from the original so they harmonise with the
// gold + warm-graphite register instead of fighting it.
const ASSET_COLOR: Record<string, string> = {
  EQUITY: "#5B7BAF",
  INDEX: "#7B6BB0",
  FX: "#5B8B5B",
  FUTURE: "#B8862B",
  CRYPTO: "#A8413A",
  ETF: "#3D8B9B",
  OPTION: "#A55B7B",
  COMMODITY: "#C9A84C",
  BOND: "#5B9B8B",
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

// ── Status token ───────────────────────────────────────────────────
function StatusToken({ status }: { status: string | undefined | null }) {
  const s = (status || "WAITING").toUpperCase();
  if (s === "READY") {
    return (
      <span
        className="label-mono inline-flex items-center"
        style={{ gap: 8, color: "hsl(var(--brand))" }}
      >
        <span
          aria-hidden
          style={{
            width: 4,
            height: 4,
            background: "hsl(var(--brand))",
            display: "inline-block",
          }}
        />
        Routing
      </span>
    );
  }
  if (s === "EXPIRED") {
    return (
      <span
        className="label-mono"
        style={{ color: "hsl(var(--fg-subtle))" }}
      >
        Window closed
      </span>
    );
  }
  if (s === "CANCELLED") {
    return (
      <span
        className="label-mono inline-flex items-center"
        style={{ gap: 8, color: "hsl(var(--neg))" }}
      >
        <span
          aria-hidden
          style={{
            width: 4,
            height: 4,
            border: "1px solid hsl(var(--neg))",
            display: "inline-block",
          }}
        />
        Cancelled
      </span>
    );
  }
  // WAITING — empty gold square
  return (
    <span
      className="label-mono inline-flex items-center"
      style={{ gap: 8, color: "hsl(var(--brand))" }}
    >
      <span
        aria-hidden
        style={{
          width: 4,
          height: 4,
          border: "1px solid hsl(var(--brand))",
          display: "inline-block",
        }}
      />
      Awaiting entry
    </span>
  );
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
    return (
      <span
        className="data-mono"
        style={{ color: "hsl(var(--fg-subtle))", fontSize: 14 }}
      >
        —
      </span>
    );
  }
  const isLong = direction === "LONG";
  const absPct = Math.abs(pct) * 100;
  const isApproaching = isLong ? pct <= 0 : pct >= 0;
  const color =
    absPct < 0.3
      ? "hsl(var(--pos))"
      : absPct < 1.0
      ? "hsl(var(--warn))"
      : "hsl(var(--fg-muted))";
  const label =
    absPct < 0.3 ? "At entry" : isApproaching ? "Approaching" : "Away";

  return (
    <div className="flex flex-col" style={{ gap: 4 }}>
      <span className="data-mono" style={{ fontSize: 14, color }}>
        {pct > 0 ? "+" : ""}
        {(pct * 100).toFixed(2)}%
      </span>
      <span className="label-mono" style={{ color }}>
        {label}
      </span>
    </div>
  );
}

// ── SL / TP tick bar ───────────────────────────────────────────────
// Brief §5.4: "reduce it to a 2px thin horizontal line with tick markers"
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

  return (
    <div className="flex flex-col" style={{ gap: 8 }}>
      <div
        style={{
          position: "relative",
          height: 2,
          width: "100%",
          background: "var(--rule-bold)",
        }}
      >
        {/* Entry tick — 8px tall vertical gold mark */}
        <div
          aria-hidden
          style={{
            position: "absolute",
            top: -3,
            height: 8,
            width: 1,
            background: "hsl(var(--brand))",
            left: `${clamp(entryPct)}%`,
            transform: "translateX(-50%)",
          }}
        />
        {/* Current price tick — 10px tall warm ivory mark */}
        <div
          aria-hidden
          style={{
            position: "absolute",
            top: -4,
            height: 10,
            width: 1,
            background: "hsl(var(--fg))",
            left: `${clamp(currentPct)}%`,
            transform: "translateX(-50%)",
            transition: "left var(--t-settle) var(--ease-paper)",
          }}
        />
      </div>
      <div
        className="flex"
        style={{
          justifyContent: "space-between",
          width: "100%",
          gap: 16,
        }}
      >
        <span
          className="data-mono"
          style={{ fontSize: 10, color: "hsl(var(--neg))" }}
        >
          SL {fmtMoney(sl)}
        </span>
        <span
          className="data-mono"
          style={{ fontSize: 10, color: "hsl(var(--pos))" }}
        >
          TP {fmtMoney(tp)}
        </span>
      </div>
    </div>
  );
}

// ── History adapter ────────────────────────────────────────────────
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

  // SSE — flash any ticker that just promoted to READY.  Preserved
  // verbatim from the previous version.
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

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const aReady = a.readiness_status === "READY" ? 0 : 1;
      const bReady = b.readiness_status === "READY" ? 0 : 1;
      if (aReady !== bReady) return aReady - bReady;
      return (b.conviction ?? 0) - (a.conviction ?? 0);
    });
  }, [filtered]);

  const histCounts = historyData?.by_status ?? {};
  const ready =
    view === "active" ? activeData?.ready_count ?? 0 : histCounts.READY ?? 0;
  const waiting =
    view === "active" ? activeData?.waiting_count ?? 0 : histCounts.WAITING ?? 0;
  const expired =
    view === "active" ? activeData?.expired_count ?? 0 : histCounts.EXPIRED ?? 0;
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
    <div className="flex flex-col" style={{ gap: 32 }}>
      {/* ── Masthead ─────────────────────────────────────────── */}
      <section>
        <p className="label-mono" style={{ color: "hsl(var(--brand))" }}>
          Signal book
        </p>
        <h1
          className="display-section"
          style={{ marginTop: 12, color: "hsl(var(--fg))" }}
        >
          What the engine wants to do next.
        </h1>
        <p className="body-prose" style={{ marginTop: 16 }}>
          <span className="data-mono" style={{ color: "hsl(var(--pos))" }}>
            {ready}
          </span>{" "}
          ready ·{" "}
          <span className="data-mono" style={{ color: "hsl(var(--brand))" }}>
            {waiting}
          </span>{" "}
          waiting · updated{" "}
          {data?.snapshot_at
            ? fmtAge((Date.now() - new Date(data.snapshot_at).getTime()) / 1000)
            : "—"}
        </p>
      </section>

      {/* ── View toggle + date range ─────────────────────────── */}
      <section
        className="flex flex-wrap items-center"
        style={{
          gap: 16,
          paddingTop: 24,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <ToggleButton
          active={view === "active"}
          onClick={() => setView("active")}
        >
          Active book
        </ToggleButton>
        <ToggleButton
          active={view === "history"}
          onClick={() => setView("history")}
        >
          Full history
        </ToggleButton>

        {view === "history" && (
          <>
            <input
              type="date"
              value={dateStart}
              onChange={(e) => setDateStart(e.target.value)}
              max={dateEnd}
              className="input-paper"
              style={{ width: 140 }}
            />
            <span
              className="label-mono"
              style={{ color: "hsl(var(--fg-subtle))" }}
            >
              →
            </span>
            <input
              type="date"
              value={dateEnd}
              onChange={(e) => setDateEnd(e.target.value)}
              min={dateStart}
              max={_today()}
              className="input-paper"
              style={{ width: 140 }}
            />
            <span
              className="label-mono"
              style={{ color: "hsl(var(--fg-subtle))" }}
            >
              {historyData?.total ?? 0} records
              {historyData?.earliest && historyData.latest && (
                <>
                  {" · "}
                  {historyData.earliest.slice(0, 16)} →{" "}
                  {historyData.latest.slice(0, 16)}
                </>
              )}
              {filled > 0 && (
                <>
                  {" · "}
                  <span style={{ color: "hsl(var(--pos))" }}>
                    {filled} filled
                  </span>
                </>
              )}
            </span>
          </>
        )}
      </section>

      {/* ── Status filter pills ──────────────────────────────── */}
      <section
        className="flex flex-wrap"
        style={{
          gap: 8,
          paddingTop: 16,
        }}
      >
        {filterPills.map((f) => (
          <FilterPill
            key={f.label}
            active={statusFilter === f.label}
            onClick={() => setStatusFilter(f.label)}
          >
            {f.label}
            {f.count > 0 && (
              <span
                className="data-mono"
                style={{ marginLeft: 8, opacity: 0.7 }}
              >
                {f.count}
              </span>
            )}
          </FilterPill>
        ))}
      </section>

      <section className="flex flex-wrap" style={{ gap: 6 }}>
        {ASSET_FILTERS.map((ac) => {
          const active = assetFilter === ac;
          const color = ac === "ALL" ? undefined : ASSET_COLOR[ac];
          return (
            <button
              key={ac}
              onClick={() => setAssetFilter(ac)}
              className="label-mono transition-colors"
              type="button"
              style={{
                padding: "5px 10px",
                background: active && color ? `${color}15` : "transparent",
                border: `1px solid ${
                  active
                    ? color ?? "hsl(var(--brand))"
                    : "var(--rule)"
                }`,
                color: active
                  ? color ?? "hsl(var(--brand))"
                  : "hsl(var(--fg-muted))",
                borderRadius: 2,
                cursor: "pointer",
                transitionDuration: "var(--t-quick)",
                transitionTimingFunction: "var(--ease-monument)",
              }}
            >
              {ac}
            </button>
          );
        })}
      </section>

      {/* ── Specimen cards ───────────────────────────────────── */}
      <section
        style={{
          paddingTop: 24,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <p
          className="label-mono"
          style={{ color: "hsl(var(--fg-subtle))", marginBottom: 16 }}
        >
          {sorted.length} {sorted.length === 1 ? "match" : "matches"} · live SL/TP,
          sizing, trade-readiness
        </p>

        {isLoading && (
          <div
            style={{
              height: 128,
              background: "hsl(var(--panel))",
              border: "1px solid var(--rule)",
              borderRadius: 6,
            }}
          />
        )}
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
          <div className="flex flex-col" style={{ gap: 8 }}>
            {sorted.map((s, idx) => {
              const ac = (s.asset_class || "EQUITY") as string;
              const color = ASSET_COLOR[ac] ?? "#64748B";
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
                <SpecimenCard
                  key={`${s.ticker}-${s.regime_fingerprint ?? "norfp"}-${idx}`}
                  flashing={isFlashing}
                >
                  {/* Header row — asset class chip + ticker + direction + strategy */}
                  <div
                    className="flex flex-wrap items-center"
                    style={{ gap: 14, marginBottom: 18 }}
                  >
                    <span
                      className="label-mono"
                      style={{
                        padding: "3px 7px",
                        color,
                        border: `1px solid ${color}50`,
                        borderRadius: 2,
                      }}
                    >
                      {ac}
                    </span>
                    <span
                      style={{
                        fontFamily:
                          'var(--font-display), "Fraunces", Georgia, serif',
                        fontSize: 24,
                        fontWeight: 600,
                        letterSpacing: "-0.02em",
                        color: "hsl(var(--fg))",
                      }}
                    >
                      {s.ticker}
                    </span>
                    <span
                      className="label-mono"
                      style={{
                        color: isLong
                          ? "hsl(var(--pos))"
                          : isHold
                          ? "hsl(var(--fg-muted))"
                          : "hsl(var(--neg))",
                      }}
                    >
                      {isLong ? "▲ Long" : isHold ? "◇ Hold" : "▼ Short"}
                    </span>
                    {s.strategy_name && (
                      <span
                        className="label-mono"
                        style={{ color: "hsl(var(--fg-subtle))" }}
                      >
                        {s.strategy_name}
                      </span>
                    )}
                  </div>

                  {/* Number grid + right rail */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 220px",
                      gap: 24,
                      marginBottom: 18,
                    }}
                    className="signal-specimen-grid"
                  >
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns:
                          "repeat(auto-fit, minmax(96px, 1fr))",
                        gap: 18,
                      }}
                    >
                      <NumCell
                        label="Current"
                        value={s.current_price ? fmtMoney(s.current_price) : "—"}
                      />
                      <NumCell
                        label="Entry"
                        value={s.entry_price ? fmtMoney(s.entry_price) : "—"}
                        valueColor="hsl(var(--brand))"
                      />
                      <NumCell
                        label="Stop loss"
                        value={
                          s.stop_loss_price ? fmtMoney(s.stop_loss_price) : "—"
                        }
                        sub={s.stop_loss_pct ? `−${fmtNumber(s.stop_loss_pct, 2)}%` : null}
                        valueColor="hsl(var(--neg))"
                      />
                      <NumCell
                        label="Target"
                        value={
                          s.take_profit_price ? fmtMoney(s.take_profit_price) : "—"
                        }
                        sub={s.take_profit_pct ? `+${fmtNumber(s.take_profit_pct, 2)}%` : null}
                        valueColor="hsl(var(--pos))"
                      />
                      <NumCell
                        label="Quantity"
                        value={qty ? fmtNumber(qty, qty < 1 ? 4 : 0) : "—"}
                        sub={qty ? qtyUnit : null}
                      />
                      <NumCell
                        label="R : R"
                        value={
                          s.risk_reward_ratio
                            ? `1:${(s.risk_reward_ratio ?? 0).toFixed(1)}`
                            : "—"
                        }
                        sub={
                          s.risk_amount
                            ? `risk ${fmtMoney(s.risk_amount)}`
                            : null
                        }
                      />
                      <div className="flex flex-col" style={{ gap: 6 }}>
                        <span className="label-mono">Distance</span>
                        <DistanceIndicator
                          pct={s.distance_to_entry_pct}
                          direction={String(s.direction)}
                        />
                      </div>
                    </div>

                    {/* Right rail — conviction bar, status token, expiry */}
                    <div className="flex flex-col" style={{ gap: 14 }}>
                      <div className="flex flex-col" style={{ gap: 6 }}>
                        <span className="label-mono">Conviction</span>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                          }}
                        >
                          <div
                            style={{
                              flex: 1,
                              height: 1,
                              background: "var(--rule-bold)",
                              position: "relative",
                            }}
                          >
                            <div
                              style={{
                                position: "absolute",
                                left: 0,
                                top: 0,
                                height: "100%",
                                width: `${conv}%`,
                                background: "hsl(var(--brand))",
                                transition:
                                  "width var(--t-settle) var(--ease-paper)",
                              }}
                            />
                          </div>
                          <span
                            className="data-mono"
                            style={{
                              fontSize: 13,
                              color: "hsl(var(--brand))",
                              minWidth: 32,
                              textAlign: "right",
                            }}
                          >
                            {conv.toFixed(0)}%
                          </span>
                        </div>
                      </div>

                      <StatusToken status={s.readiness_status} />

                      {s.expires_at && (
                        <span
                          className="label-mono"
                          style={{ color: "hsl(var(--fg-subtle))" }}
                        >
                          Expires{" "}
                          {fmtAge(
                            (new Date(s.expires_at).getTime() - Date.now()) /
                              1000,
                          )}
                        </span>
                      )}

                      {s.notional_value && s.notional_value > 0 && (
                        <span
                          className="label-mono"
                          style={{ color: "hsl(var(--fg-subtle))" }}
                        >
                          Notional {fmtMoney(s.notional_value)}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* SL/TP bar + rationale */}
                  <div
                    className="flex flex-wrap items-end"
                    style={{ gap: 24, justifyContent: "space-between" }}
                  >
                    <div style={{ flex: 1, minWidth: 220, maxWidth: 320 }}>
                      <SlTpBar signal={s} />
                    </div>
                    {s.rationale && (
                      <p
                        className="display-callout"
                        style={{
                          fontSize: 14,
                          flex: 2,
                          minWidth: 220,
                          maxWidth: "60ch",
                          margin: 0,
                        }}
                      >
                        “{s.rationale}”
                      </p>
                    )}
                  </div>

                  {s.readiness_status === "WAITING" && s.readiness_reason && (
                    <p
                      className="label-mono"
                      style={{
                        marginTop: 12,
                        color: "hsl(var(--warn))",
                      }}
                    >
                      ◌ {s.readiness_reason}
                    </p>
                  )}
                </SpecimenCard>
              );
            })}
          </div>
        )}
      </section>

      <style jsx global>{`
        @keyframes specimen-flash {
          0%,
          100% {
            border-color: var(--rule);
          }
          50% {
            border-color: hsl(var(--brand) / 0.7);
          }
        }
        .specimen-flashing {
          animation: specimen-flash 0.6s var(--ease-paper) 3;
        }
        @media (max-width: 760px) {
          .signal-specimen-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </div>
  );
}

/* ─── Sub-components ──────────────────────────────────────────────── */

function SpecimenCard({
  flashing,
  children,
}: {
  flashing: boolean;
  children: React.ReactNode;
}) {
  return (
    <article
      className={flashing ? "card-paper specimen-flashing" : "card-paper"}
      style={{ padding: "24px 28px" }}
    >
      {children}
    </article>
  );
}

function NumCell({
  label,
  value,
  sub,
  valueColor,
}: {
  label: string;
  value: string;
  sub?: string | null;
  valueColor?: string;
}) {
  return (
    <div className="flex flex-col" style={{ gap: 4 }}>
      <span className="label-mono">{label}</span>
      <span
        className="data-mono"
        style={{
          fontSize: 16,
          fontWeight: 600,
          color: valueColor ?? "hsl(var(--fg))",
        }}
      >
        {value}
      </span>
      {sub && (
        <span
          className="data-mono"
          style={{
            fontSize: 10,
            color: valueColor
              ? `${valueColor}`
              : "hsl(var(--fg-subtle))",
            opacity: valueColor ? 0.8 : 1,
          }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="label-mono transition-colors"
      style={{
        padding: "8px 16px",
        background: active ? "hsl(var(--brand))" : "transparent",
        color: active ? "hsl(var(--brand-fg))" : "hsl(var(--fg-muted))",
        border: `1px solid ${active ? "hsl(var(--brand))" : "var(--rule-bold)"}`,
        borderRadius: 4,
        cursor: "pointer",
        transitionDuration: "var(--t-quick)",
        transitionTimingFunction: "var(--ease-monument)",
      }}
    >
      {children}
    </button>
  );
}

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="label-mono transition-colors"
      style={{
        padding: "6px 12px",
        background: active ? "hsl(var(--brand) / 0.10)" : "transparent",
        color: active ? "hsl(var(--brand))" : "hsl(var(--fg-muted))",
        border: `1px solid ${active ? "hsl(var(--brand))" : "var(--rule)"}`,
        borderRadius: 2,
        cursor: "pointer",
        transitionDuration: "var(--t-quick)",
        transitionTimingFunction: "var(--ease-monument)",
      }}
    >
      {children}
    </button>
  );
}
