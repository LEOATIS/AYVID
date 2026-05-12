"use client";
import { Briefcase } from "lucide-react";
import { Empty } from "@/components/ui/Empty";
import { useEquity, usePositions, useRunner, useTrades } from "@/lib/hooks";
import { fmtMoney, fmtNumber, fmtPct, fmtTime, fmtDate } from "@/lib/format";

/**
 * AYVID portfolio — private-bank holding statement, per brief §5.5.
 *
 *  • Eyebrow ".label-mono · POSITIONS · LIVE" + Fraunces section title.
 *  • Equity / Cash / Position Value / Open positions = row of text, no cards.
 *  • Open-positions table:
 *      - table-layout: fixed, border-collapse: collapse
 *      - th: .label-mono, padding 12px 16px, border-bottom 1px var(--rule-bold)
 *      - td: .data-mono 14px, padding 12px 16px, border-bottom 1px var(--rule)
 *      - row hover: background hsl(var(--bg-1)), no transform
 *      - LONG / SHORT: 4px tall vertical coloured bar to the left of symbol
 *      - Negative P&L: var(--neg) (terracotta), positive: var(--pos) (forest)
 *  • Recent fills: identical treatment — the two tables read as a matched
 *    pair, like assets and transactions on a quarterly statement.
 *
 * Every existing hook + data shape is preserved.
 */

interface PositionRow {
  ticker?: string;
  symbol?: string;
  quantity?: number;
  qty?: number;
  avg_price?: number;
  avg_cost?: number;
  market_price?: number;
  mark_price_timestamp?: string | null;
  mark_price_source?: string | null;
  mark_age_seconds?: number | null;
  market_value?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  side?: string;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  stop_loss_pct?: number | null;
  take_profit_pct?: number | null;
  asset_class?: string | null;
  strategy?: string | null;
  strategy_name?: string | null;
}

interface TradeRow {
  timestamp?: string;
  ticker?: string;
  symbol?: string;
  side?: string;
  action?: string;
  quantity?: number;
  qty?: number;
  shares?: number;
  price?: number;
  fill_price?: number;
  notional?: number;
  status?: string;
}

const STALE_WARN_SEC = 600; // 10 min — amber
const STALE_BAD_SEC = 3600; // 1 hr  — terracotta

function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function ageColor(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "hsl(var(--fg-muted))";
  if (seconds >= STALE_BAD_SEC) return "hsl(var(--neg))";
  if (seconds >= STALE_WARN_SEC) return "hsl(var(--warn))";
  return "hsl(var(--pos))";
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

  const cashLatest = equity?.snapshots?.[equity.snapshots.length - 1]?.cash;
  const posLatest =
    equity?.snapshots?.[equity.snapshots.length - 1]?.position_value;
  const totalReturnPct = summary?.total_return_pct ?? 0;

  return (
    <div className="flex flex-col" style={{ gap: 48 }}>
      {/* ── Masthead ─────────────────────────────────────────── */}
      <section>
        <p className="label-mono" style={{ color: "hsl(var(--brand))" }}>
          Positions · Live
        </p>
        <h1
          className="display-section"
          style={{ marginTop: 12, color: "hsl(var(--fg))" }}
        >
          Holdings statement.
        </h1>
        <p className="body-prose" style={{ marginTop: 16 }}>
          Open positions, realised and unrealised P&amp;L, and recent fills from
          the live IB paper account. Issued{" "}
          {positions?.as_of ? fmtTime(positions.as_of) : "—"}.
        </p>
      </section>

      {/* ── Top-of-statement KPI row (no cards) ──────────────── */}
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
        className="portfolio-kpi-grid"
      >
        <KpiCell
          first
          label="Equity"
          value={fmtMoney(final, { decimals: 0 })}
          delta={`${totalReturnPct >= 0 ? "+" : ""}${fmtPct(totalReturnPct)}`}
          deltaColor={
            totalReturnPct >= 0 ? "hsl(var(--pos))" : "hsl(var(--neg))"
          }
          hint={`vs ${fmtMoney(initial, { decimals: 0 })} start`}
        />
        <KpiCell
          label="Cash"
          value={fmtMoney(cashLatest, { decimals: 0 })}
          hint="Settled, available for entries"
        />
        <KpiCell
          label="Position value"
          value={fmtMoney(posLatest, { decimals: 0 })}
          hint="Marked-to-market"
        />
        <KpiCell
          label="Open positions"
          value={fmtNumber(summary?.open_positions ?? 0)}
          hint={`Mode · ${runner?.mode ?? "—"}`}
        />
      </section>

      {/* ── Open positions ─────────────────────────────────── */}
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
              Open positions
            </p>
            <p
              className="body-prose"
              style={{ marginTop: 8, maxWidth: "70ch" }}
            >
              As of {positions?.as_of ? fmtTime(positions.as_of) : "—"}
              {positions?.mode === "shadow" && (
                <>
                  {" "}
                  ·{" "}
                  <span
                    className="label-mono"
                    style={{ color: "hsl(var(--warn))" }}
                  >
                    Shadow mode
                  </span>
                </>
              )}
              {positions?.max_mark_age_seconds != null &&
                positions.max_mark_age_seconds >= STALE_WARN_SEC && (
                  <>
                    {" "}
                    ·{" "}
                    <span
                      className="label-mono"
                      style={{
                        color:
                          positions.max_mark_age_seconds >= STALE_BAD_SEC
                            ? "hsl(var(--neg))"
                            : "hsl(var(--warn))",
                      }}
                    >
                      Marks {formatAge(positions.max_mark_age_seconds)}
                    </span>
                  </>
                )}
            </p>
          </div>
          <span
            className="label-mono"
            style={{ color: "hsl(var(--fg-subtle))" }}
          >
            {positionRows.length} position{positionRows.length === 1 ? "" : "s"}
          </span>
        </header>

        <div style={{ marginTop: 24 }}>
          {positionRows.length === 0 ? (
            <Empty
              icon={<Briefcase className="h-8 w-8" />}
              title="No open positions"
              hint="The runner has not opened any positions yet, or this snapshot is empty."
            />
          ) : (
            <StatementTable
              columns={[
                { label: "Symbol", align: "left", width: "auto" },
                { label: "Side", align: "left", width: "80px" },
                { label: "Qty", align: "right" },
                { label: "Avg", align: "right" },
                { label: "Mark", align: "right" },
                { label: "Mark age", align: "left" },
                {
                  label: "Stop loss",
                  align: "right",
                  color: "hsl(var(--neg))",
                },
                {
                  label: "Target",
                  align: "right",
                  color: "hsl(var(--pos))",
                },
                { label: "Market value", align: "right" },
                { label: "Unrealised P&L", align: "right" },
              ]}
            >
              {positionRows.map((p, i) => {
                const sym = p.ticker ?? p.symbol ?? "—";
                const qty = p.quantity ?? p.qty ?? 0;
                const upnl = p.unrealized_pnl ?? 0;
                const sideLong = (
                  p.side ?? (qty >= 0 ? "LONG" : "SHORT")
                ).toUpperCase();
                const avgEntry = p.avg_price ?? p.avg_cost ?? null;
                const slPrice =
                  p.stop_loss_price ??
                  (avgEntry != null && p.stop_loss_pct != null
                    ? sideLong === "LONG"
                      ? avgEntry * (1 - p.stop_loss_pct / 100)
                      : avgEntry * (1 + p.stop_loss_pct / 100)
                    : null);
                const tpPrice =
                  p.take_profit_price ??
                  (avgEntry != null && p.take_profit_pct != null
                    ? sideLong === "LONG"
                      ? avgEntry * (1 + p.take_profit_pct / 100)
                      : avgEntry * (1 - p.take_profit_pct / 100)
                    : null);
                const sideColor =
                  sideLong === "LONG"
                    ? "hsl(var(--pos))"
                    : "hsl(var(--neg))";
                const ageColr = ageColor(p.mark_age_seconds);
                return (
                  <StatementRow key={`${sym}-${i}`}>
                    <SymbolCell symbol={sym} sideColor={sideColor} />
                    <td style={tdStyle("left")}>
                      <span
                        className="label-mono"
                        style={{ color: sideColor }}
                      >
                        {sideLong}
                      </span>
                    </td>
                    <td style={tdStyle("right")}>{fmtNumber(qty)}</td>
                    <td style={tdStyle("right", "hsl(var(--fg-muted))")}>
                      {fmtMoney(avgEntry)}
                    </td>
                    <td style={tdStyle("right", "hsl(var(--fg-muted))")}>
                      {fmtMoney(p.market_price)}
                    </td>
                    <td style={tdStyle("left")}>
                      <span
                        className="label-mono"
                        style={{ color: ageColr }}
                        title={`source=${p.mark_price_source ?? "?"} ts=${
                          p.mark_price_timestamp ?? "?"
                        }`}
                      >
                        {formatAge(p.mark_age_seconds)}
                      </span>
                    </td>
                    <td style={tdStyle("right", "hsl(var(--neg))")}>
                      {slPrice != null ? fmtMoney(slPrice) : "—"}
                      {p.stop_loss_pct != null && (
                        <div
                          style={{
                            fontSize: 10,
                            color: "hsl(var(--neg) / 0.7)",
                            marginTop: 2,
                          }}
                        >
                          −{p.stop_loss_pct.toFixed(1)}%
                        </div>
                      )}
                    </td>
                    <td style={tdStyle("right", "hsl(var(--pos))")}>
                      {tpPrice != null ? fmtMoney(tpPrice) : "—"}
                      {p.take_profit_pct != null && (
                        <div
                          style={{
                            fontSize: 10,
                            color: "hsl(var(--pos) / 0.7)",
                            marginTop: 2,
                          }}
                        >
                          +{p.take_profit_pct.toFixed(1)}%
                        </div>
                      )}
                    </td>
                    <td style={tdStyle("right")}>
                      {fmtMoney(p.market_value)}
                    </td>
                    <td
                      style={tdStyle(
                        "right",
                        upnl >= 0 ? "hsl(var(--pos))" : "hsl(var(--neg))",
                      )}
                    >
                      {fmtMoney(upnl)}
                    </td>
                  </StatementRow>
                );
              })}
            </StatementTable>
          )}
        </div>
      </section>

      {/* ── Recent fills ────────────────────────────────────── */}
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
              Recent fills
            </p>
            <p className="body-prose" style={{ marginTop: 8 }}>
              Latest trades executed by the live runner.
            </p>
          </div>
          <span
            className="label-mono"
            style={{ color: "hsl(var(--fg-subtle))" }}
          >
            {tradeRows.length} fill{tradeRows.length === 1 ? "" : "s"}
          </span>
        </header>

        <div style={{ marginTop: 24 }}>
          {tradeRows.length === 0 ? (
            <Empty
              icon={<Briefcase className="h-8 w-8" />}
              title="No fills yet"
              hint="The runner has not executed any trades. Fills appear here when AUTO_EXECUTE is on or you trigger /api/v1/trade/execute."
            />
          ) : (
            <StatementTable
              columns={[
                { label: "Time", align: "left" },
                { label: "Symbol", align: "left" },
                { label: "Side", align: "left", width: "80px" },
                { label: "Qty", align: "right" },
                { label: "Price", align: "right" },
                { label: "Notional", align: "right" },
                { label: "Status", align: "left", width: "120px" },
              ]}
            >
              {tradeRows.map((t, i) => {
                const sym = t.ticker ?? t.symbol ?? "—";
                const side = (t.side ?? t.action ?? "—").toUpperCase();
                const qtyRaw = t.quantity ?? t.qty ?? t.shares ?? 0;
                const qty = Math.abs(Number(qtyRaw) || 0);
                const price = t.price ?? t.fill_price;
                const status = (t.status ?? "—").toUpperCase();
                const sideColor =
                  side.includes("BUY") || side === "LONG"
                    ? "hsl(var(--pos))"
                    : "hsl(var(--neg))";
                return (
                  <StatementRow key={`${sym}-${i}`}>
                    <td style={tdStyle("left", "hsl(var(--fg-muted))")}>
                      {fmtTime(t.timestamp)}
                    </td>
                    <SymbolCell symbol={sym} sideColor={sideColor} />
                    <td style={tdStyle("left")}>
                      <span
                        className="label-mono"
                        style={{ color: sideColor }}
                      >
                        {side}
                      </span>
                    </td>
                    <td style={tdStyle("right")}>{fmtNumber(qty)}</td>
                    <td style={tdStyle("right")}>{fmtMoney(price)}</td>
                    <td style={tdStyle("right")}>{fmtMoney(t.notional)}</td>
                    <td style={tdStyle("left")}>
                      <span
                        className="label-mono"
                        style={{
                          color:
                            status === "FILLED"
                              ? "hsl(var(--pos))"
                              : "hsl(var(--fg-muted))",
                        }}
                      >
                        {status}
                      </span>
                    </td>
                  </StatementRow>
                );
              })}
            </StatementTable>
          )}
        </div>
      </section>

      {/* ── Run metadata ─────────────────────────────────────── */}
      <section
        style={{
          paddingTop: 32,
          borderTop: "1px solid var(--rule)",
        }}
      >
        <p className="label-mono" style={{ color: "hsl(var(--fg-subtle))" }}>
          Run metadata
        </p>
        <dl
          style={{
            marginTop: 16,
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 24,
          }}
          className="metadata-grid"
        >
          <Meta label="Started" value={fmtDate(equity?.meta?.started_at)} />
          <Meta label="Mode" value={equity?.meta?.mode ?? "—"} />
          <Meta
            label="IB endpoint"
            value={`${equity?.meta?.ib_host}:${equity?.meta?.ib_port}`}
          />
          <Meta
            label="Auto execute"
            value={equity?.meta?.auto_execute ? "On" : "Off"}
          />
        </dl>
      </section>

      <style jsx>{`
        @media (max-width: 800px) {
          :global(.portfolio-kpi-grid) {
            grid-template-columns: repeat(2, 1fr) !important;
            row-gap: 24px;
          }
          :global(.portfolio-kpi-grid > div:nth-child(2n + 1)) {
            border-left: none !important;
            padding-left: 0 !important;
          }
        }
        @media (max-width: 600px) {
          :global(.metadata-grid) {
            grid-template-columns: repeat(2, 1fr) !important;
            row-gap: 16px;
          }
        }
      `}</style>
    </div>
  );
}

/* ─── Sub-components ──────────────────────────────────────────────── */

function KpiCell({
  label,
  value,
  delta,
  deltaColor,
  hint,
  first,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: string;
  hint?: string;
  first?: boolean;
}) {
  return (
    <div
      style={{
        paddingLeft: first ? 0 : 24,
        paddingRight: 24,
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
          fontSize: "clamp(26px, 2.6vw, 36px)",
          lineHeight: 1.05,
          fontWeight: 600,
          color: "hsl(var(--fg))",
          letterSpacing: "-0.02em",
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
          style={{ fontSize: 11, color: deltaColor }}
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
            color: "hsl(var(--fg-subtle))",
            lineHeight: 1.4,
          }}
        >
          {hint}
        </span>
      )}
    </div>
  );
}

/** A holding-statement table — fixed layout, hairline rules, mono body. */
function StatementTable({
  columns,
  children,
}: {
  columns: { label: string; align: "left" | "right"; width?: string; color?: string }[];
  children: React.ReactNode;
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          tableLayout: "fixed",
          minWidth: 720,
        }}
      >
        <colgroup>
          {columns.map((c) => (
            <col
              key={c.label}
              style={{ width: c.width ?? undefined }}
            />
          ))}
        </colgroup>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--rule-bold)" }}>
            {columns.map((c) => (
              <th
                key={c.label}
                className="label-mono"
                style={{
                  padding: "12px 16px",
                  textAlign: c.align,
                  fontWeight: 500,
                  color: c.color ?? "hsl(var(--fg-subtle))",
                }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function StatementRow({ children }: { children: React.ReactNode }) {
  return (
    <tr
      className="statement-row"
      style={{ borderBottom: "1px solid var(--rule)" }}
    >
      {children}
      <style jsx>{`
        :global(.statement-row:hover) {
          background: hsl(var(--bg-elev));
        }
      `}</style>
    </tr>
  );
}

function SymbolCell({
  symbol,
  sideColor,
}: {
  symbol: string;
  sideColor: string;
}) {
  return (
    <td
      style={{
        padding: 0,
        position: "relative",
      }}
    >
      {/* 4px vertical bar to the left of the symbol cell — coloured by
          side (LONG → pos, SHORT → neg).  Replaces the rounded pill. */}
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: 0,
          top: "50%",
          transform: "translateY(-50%)",
          width: 2,
          height: 18,
          background: sideColor,
        }}
      />
      <span
        className="data-mono"
        style={{
          display: "inline-block",
          padding: "12px 16px 12px 16px",
          fontSize: 14,
          color: "hsl(var(--fg))",
          fontWeight: 600,
        }}
      >
        {symbol}
      </span>
    </td>
  );
}

function tdStyle(
  align: "left" | "right",
  color?: string,
): React.CSSProperties {
  return {
    padding: "12px 16px",
    textAlign: align,
    fontFamily: 'var(--font-mono), "JetBrains Mono", ui-monospace, monospace',
    fontSize: 14,
    fontVariantNumeric: "tabular-nums lining-nums",
    color: color ?? "hsl(var(--fg))",
  };
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col" style={{ gap: 6 }}>
      <dt className="label-mono">{label}</dt>
      <dd
        className="data-mono"
        style={{
          fontSize: 14,
          color: "hsl(var(--fg))",
        }}
      >
        {value}
      </dd>
    </div>
  );
}
