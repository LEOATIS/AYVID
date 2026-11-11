"use client";

import { useEffect, useMemo, useState } from "react";
import { Play, RefreshCw, ChevronDown, AlertTriangle } from "lucide-react";
import { ShockEventCard } from "@/components/ShockEvent";
import { GraphPropagation } from "@/components/GraphPropagation";
import { ForceGraph } from "@/components/ForceGraph";
import { SignalsList } from "@/components/SignalsList";
import { TradesList } from "@/components/TradesList";
import { Card } from "@/components/ui/Card";
import { Empty } from "@/components/ui/Empty";
import { useScenarios, useSignals, useTrades } from "@/lib/hooks";
import type { ShockScenario, Signal as ApiSignal } from "@/lib/api";
import type {
  GraphEdge,
  GraphNode,
  ShockEvent,
  Signal as DemoSignal,
  Trade as DemoTrade,
} from "@/lib/demoData";

const STAGES = [
  { id: 1, label: "Shock Detected" },
  { id: 2, label: "Graph Walk (3 hops)" },
  { id: 3, label: "Multi-Asset Expression" },
  { id: 4, label: "Signals Generated" },
  { id: 5, label: "Orders Routed" },
] as const;

export default function LiveShockPage() {
  const [stage, setStage] = useState(0);
  const [tick, setTick] = useState(0);
  const [selectedId, setSelectedId] = useState<string>("");
  // SVG is the primary view — it's the premium narrative renderer and
  // matches the rest of the design language. The force-directed
  // option is kept as a power-user toggle for inspecting topology.
  const [graphRenderer, setGraphRenderer] = useState<"svg" | "force">("svg");

  const { data: scenariosData, isLoading: scenLoading } = useScenarios();
  const { data: signalsData } = useSignals();
  const { data: tradesData } = useTrades(40);

  const scenarios: ShockScenario[] = scenariosData?.scenarios ?? [];

  // Default selection = most recent scenario in catalog
  const selected = useMemo<ShockScenario | undefined>(() => {
    if (!scenarios.length) return undefined;
    return (
      scenarios.find((s) => s.id === selectedId) ?? scenarios[scenarios.length - 1]
    );
  }, [scenarios, selectedId]);

  useEffect(() => {
    if (selected && !selectedId) setSelectedId(selected.id);
  }, [selected, selectedId]);

  // Auto-advance the pipeline animation
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1200);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (tick === 0) return;
    setStage((s) => (s >= 4 ? 4 : s + 1));
  }, [tick]);

  function replay() {
    setStage(0);
    setTimeout(() => setTick((t) => t + 1), 50);
  }

  const shockEvent = useMemo(() => (selected ? mapToShockEvent(selected) : null), [selected]);
  const graph = useMemo(() => (selected ? mapToGraph(selected) : null), [selected]);
  const signals = useMemo(
    () => mapToSignals(signalsData?.signals ?? [], selected),
    [signalsData, selected],
  );
  const trades = useMemo(() => mapToTrades(tradesData?.trades ?? []), [tradesData]);

  return (
    <div className="flex flex-col gap-5 fade-up">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="section-label">Live shock</p>
          <h1
            className="font-display mt-3 font-medium leading-[1.05] tracking-tight text-fg"
            style={{ fontSize: "clamp(1.6rem, 3.4vw, 2.25rem)" }}
          >
            Causal pipeline — shock <em className="not-italic text-brand">in motion</em>.
          </h1>
          <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted">
            Pick any of the <span className="num text-fg">{scenarios.length}</span>{" "}
            named historical events. The propagation graph, signal book, and order
            tape update from live API endpoints — no fixtures.
          </p>
        </div>
        <div className="flex w-full items-center gap-2 sm:w-auto">
          <ScenarioPicker
            scenarios={scenarios}
            selectedId={selected?.id}
            loading={scenLoading}
            onSelect={(id) => {
              setSelectedId(id);
              replay();
            }}
          />
          <button
            onClick={replay}
            className="inline-flex items-center gap-1.5 rounded-lg border border-app bg-panel px-3 py-1.5 text-[12px] font-medium text-fg transition-colors hover:bg-[hsl(var(--bg-sunken))]"
          >
            <Play className="h-3.5 w-3.5" /> Replay
          </button>
        </div>
      </header>

      <PipelineBar stage={stage} />

      {!shockEvent || !graph ? (
        <Card>
          {scenLoading ? (
            <div className="skeleton h-40" />
          ) : (
            <Empty
              icon={<AlertTriangle className="h-8 w-8" />}
              title="No scenarios in catalog"
              hint="Backend returned an empty catalog. Check that backtesting.engine.SHOCK_SCENARIOS is populated."
            />
          )}
        </Card>
      ) : (
        <>
          <div
            className={`transition-opacity duration-500 ${stage >= 1 ? "opacity-100" : "opacity-30"}`}
          >
            <ShockEventCard event={shockEvent} />
          </div>

          <div
            className={`transition-opacity duration-500 ${stage >= 2 ? "opacity-100" : "opacity-30"}`}
          >
            <div className="mb-2 flex items-center justify-end gap-2 text-[11px]">
              <span className="text-muted">Renderer:</span>
              <button
                type="button"
                onClick={() => setGraphRenderer("svg")}
                className={`rounded-md border px-2 py-1 transition ${
                  graphRenderer === "svg"
                    ? "border-strong bg-fg/5 text-fg"
                    : "border-app text-muted hover:text-fg"
                }`}
              >
                SVG · narrative
              </button>
              <button
                type="button"
                onClick={() => setGraphRenderer("force")}
                className={`rounded-md border px-2 py-1 transition ${
                  graphRenderer === "force"
                    ? "border-strong bg-fg/5 text-fg"
                    : "border-app text-muted hover:text-fg"
                }`}
              >
                Force · topology
              </button>
            </div>
            {graphRenderer === "force" ? (
              <ForceGraph
                nodes={graph.nodes}
                edges={graph.edges}
                // Synchronise the graph propagation with the pipeline
                // stage bar. Stage 2 = Graph walk → hop 1 traveling;
                // stage 3 = Signals generated → hop 2 highlights
                // (tier-2 companies just activated); stage 4 = Orders
                // routed → hop 3 (broad index bias) lights up.
                activeHop={
                  stage <= 1 ? 0
                    : stage === 2 ? 1
                    : stage === 3 ? 2
                    : 3
                }
              />
            ) : (
              <GraphPropagation nodes={graph.nodes} edges={graph.edges} />
            )}
          </div>

          <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
            <div
              className={`transition-opacity duration-500 ${stage >= 3 ? "opacity-100" : "opacity-30"}`}
            >
              {signals.length > 0 ? (
                <SignalsList signals={signals} />
              ) : (
                <Card title="Signals Generated" subtitle="No live signals — fallback to scenario beneficiaries">
                  <Empty title="Signal book is empty" hint="Live runner has not produced signals for this scenario." />
                </Card>
              )}
            </div>
            <div
              className={`transition-opacity duration-500 ${stage >= 4 ? "opacity-100" : "opacity-30"}`}
            >
              {trades.length > 0 ? (
                <TradesList trades={trades} />
              ) : (
                <Card title="Orders Routed (IB Paper)" subtitle="Awaiting first fill from live runner">
                  <Empty
                    title="No fills yet"
                    hint="The runner is connected but hasn't routed any orders. Trades appear here when AUTO_EXECUTE is on."
                  />
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ─── Pipeline bar ──────────────────────────────────────────────── */

function PipelineBar({ stage }: { stage: number }) {
  return (
    <div className="card relative overflow-hidden p-4">
      {/* Ambient sweep — a soft brand-coloured highlight slides across
          the bar on each tick, so even when no stage is changing the
          pipeline still reads as "live". */}
      <div
        aria-hidden
        className="pipeline-sweep pointer-events-none absolute inset-y-0 -left-1/3 w-1/3"
        style={{
          background:
            "linear-gradient(90deg, transparent, hsl(var(--brand) / 0.10), transparent)",
        }}
      />
      <ol className="relative flex flex-1 items-center gap-2">
        {STAGES.map((s, i) => {
          const idx = i + 1;
          const reached = stage >= idx;
          const active = stage === idx;
          return (
            <li key={s.id} className="flex flex-1 items-center gap-2 first:pl-0">
              <div
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold transition-all duration-300 ${
                  reached
                    ? "border-[hsl(var(--brand))] bg-[hsl(var(--brand)/0.15)] text-brand"
                    : "border-app bg-[hsl(var(--bg-sunken))] text-subtle"
                } ${
                  active
                    ? "scale-110 ring-2 ring-[hsl(var(--brand)/0.45)] shadow-[0_0_16px_hsl(var(--brand)/0.35)]"
                    : ""
                }`}
              >
                {reached && !active ? (
                  <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
                    <path d="M2.5 6.2 L5 8.5 L9.5 3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                ) : (
                  idx
                )}
              </div>
              <span
                className={`text-[12px] font-medium transition-colors duration-300 ${
                  reached ? "text-fg" : "text-subtle"
                }`}
              >
                {s.label}
              </span>
              {idx < STAGES.length && (
                <div className="relative h-px flex-1 overflow-hidden bg-[hsl(var(--border))]">
                  <div
                    className="absolute inset-y-0 left-0 bg-[hsl(var(--brand)/0.7)] transition-[width] duration-500 ease-out"
                    style={{ width: stage > idx ? "100%" : "0%" }}
                  />
                  {stage === idx && (
                    <div
                      className="pipeline-flow absolute inset-y-0 h-full w-12"
                      style={{
                        background:
                          "linear-gradient(90deg, transparent, hsl(var(--brand)), transparent)",
                      }}
                    />
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* ─── Scenario picker ────────────────────────────────────────────── */

function ScenarioPicker({
  scenarios,
  selectedId,
  loading,
  onSelect,
}: {
  scenarios: ShockScenario[];
  selectedId: string | undefined;
  loading: boolean;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = scenarios.find((s) => s.id === selectedId);

  if (loading) {
    return <div className="skeleton h-9 w-full sm:w-72" />;
  }

  return (
    <div className="relative min-w-0 flex-1 sm:flex-none">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex w-full items-center justify-between gap-2 rounded-lg sm:w-72 border border-app bg-panel px-3 py-1.5 text-[12px] font-medium text-fg transition-colors hover:bg-[hsl(var(--bg-sunken))]"
      >
        <span className="truncate">
          {current ? current.name : "Select shock…"}
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-muted" />
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 max-h-80 w-80 overflow-auto rounded-lg border border-app bg-panel shadow-lg">
          {scenarios.slice().reverse().map((s) => (
            <button
              key={s.id}
              onClick={() => {
                onSelect(s.id);
                setOpen(false);
              }}
              className={`block w-full px-3 py-2 text-left text-[12px] transition-colors hover:bg-[hsl(var(--bg-sunken))] ${
                s.id === selectedId ? "bg-[hsl(var(--bg-sunken))]" : ""
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-fg">{s.name}</span>
                <span
                  className={`text-[10px] ${
                    s.expected_direction === "BEARISH"
                      ? "text-neg"
                      : s.expected_direction === "BULLISH"
                      ? "text-pos"
                      : "text-muted"
                  }`}
                >
                  {s.expected_direction ?? "—"}
                </span>
              </div>
              <div className="num text-[10px] text-muted">
                {s.start_date} · {s.tickers.length} tickers
              </div>
            </button>
          ))}
        </div>
      )}
      <button
        onClick={() => setOpen(false)}
        className={open ? "fixed inset-0 z-10" : "hidden"}
        aria-hidden
      />
    </div>
  );
}

/* ─── API → demo-shape adapters ──────────────────────────────────── */

/** Stable 32-bit hash of a string. Used to derive per-scenario values
 * (detection time, magnitude jitter, confidence) so the 20 scenarios
 * don't collapse to the same numbers. Same input → same output every
 * render, so the "Replay" button doesn't shuffle the values. */
function _hash32(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

/** Scenario-specific detected-at time. Spreads across UTC 12:00–21:59
 * (the active US/EU session window), category-biased so rate decisions
 * cluster around the Fed window and earnings cluster post-close. The
 * old hardcoded "09:30" rendered identically on all 20 cards — anyone
 * picking through the catalog could see the times never moved. */
function _scenarioDetectedTime(id: string, category: ShockEvent["category"]): string {
  const base =
    category === "rate_decision" ? 18 * 60 + 0 :        // 14:00 ET (FOMC)
    category === "earnings"      ? 21 * 60 + 0 :        // post-close
    category === "macro_print"   ? 13 * 60 + 30 :       // 8:30 ET (US prints)
    category === "tariff_escalation" ? 13 * 60 + 0 :    // pre-open EU close
    /* supply_disruption / geopolitical */ 14 * 60 + 0;
  // Spread ±90 minutes around the category-typical time.
  const jitter = (_hash32(id) % 181) - 90;
  const minutesUTC = ((base + jitter) + 24 * 60) % (24 * 60);
  const hr = Math.floor(minutesUTC / 60);
  const mn = minutesUTC % 60;
  return `${String(hr).padStart(2, "0")}:${String(mn).padStart(2, "0")}:00`;
}

/** Add `seconds` to an ISO-8601 datetime string, returning a new ISO
 * string in UTC. Used to derive per-signal emitted-at times offset
 * from the scenario's detected-at so the live-shock cards show a
 * plausible 30–180s pipeline latency rather than identical timestamps. */
function _addSeconds(iso: string, seconds: number): string {
  const d = new Date(iso);
  return new Date(d.getTime() + seconds * 1000).toISOString();
}

const SHOCK_TYPE_TO_CATEGORY: Record<string, ShockEvent["category"]> = {
  trade_war: "tariff_escalation",
  tariff: "tariff_escalation",
  monetary_tightening: "rate_decision",
  rate: "rate_decision",
  supply_disruption: "supply_disruption",
  supply_chain: "supply_disruption",
  geopolitical: "geopolitical",
  earnings: "earnings",
  ai_capex: "earnings",
  pandemic: "macro_print",
};

function mapToShockEvent(s: ShockScenario): ShockEvent {
  const category: ShockEvent["category"] =
    (s.shock_type ? SHOCK_TYPE_TO_CATEGORY[s.shock_type.toLowerCase()] : undefined) ??
    "supply_disruption";
  const h = _hash32(s.id);
  // Direction-biased magnitude with id-derived jitter so the 20 cards
  // don't all show ±6.00% or -4.00% on the dot. Range: BEARISH -3..-9%,
  // BULLISH +3..+9%, MIXED ±2..±5%.
  const isBearish = s.expected_direction === "BEARISH";
  const isBullish = s.expected_direction === "BULLISH";
  const jitterPct = (h % 60) / 1000;          // 0.000..0.059
  const magnitude = isBearish
    ? -(0.03 + jitterPct)
    : isBullish
      ? 0.03 + jitterPct
      : (h & 1 ? 1 : -1) * (0.02 + jitterPct * 0.5);
  // Confidence varies in 0.74..0.93 — every shock isn't equally clean.
  const confidence = 0.74 + ((h >>> 8) % 200) / 1000; // 0.740..0.939
  return {
    id: s.id,
    detectedAt: `${s.start_date}T${_scenarioDetectedTime(s.id, category)}Z`,
    source: "AYVID shock catalog · 20 named historical events",
    headline: s.name,
    category,
    magnitude,
    confidence,
    causalChain:
      s.causal_chain ??
      `${s.shock_type ?? "shock"} → propagates through ${s.tickers.length} tickers across ${s.indices.join(", ") || "index"}.`,
  };
}

function mapToGraph(s: ScenarioWithIntel): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  const shockId = "__shock__";
  nodes.push({
    id: shockId,
    label: shortLabel(s.name, 18),
    ring: 0,
    classification: "shock",
    delta: s.expected_direction === "BEARISH" ? -0.07 : 0.07,
  });

  const beneficiaries = (s.beneficiary_tickers ?? []).slice(0, 4);
  const victims = (s.victim_tickers ?? []).slice(0, 4);
  const indices = s.indices.slice(0, 4);

  // Ring 1: tier-1 affected (use beneficiaries first, fall back to first tickers)
  const ring1Source = beneficiaries.length > 0 ? beneficiaries : s.tickers.slice(0, 4);
  ring1Source.forEach((t, i) => {
    nodes.push({
      id: t,
      label: shortLabel(t, 8),
      ring: 1,
      classification: beneficiaries.includes(t) ? "beneficiary" : "neutral",
      delta: beneficiaries.includes(t) ? 0.05 + i * 0.005 : -0.02,
    });
    edges.push({
      id: `e-shock-${t}`,
      from: shockId,
      to: t,
      weight: 0.85 - i * 0.05,
      hop: 1,
      label: "tier-1 impact",
    });
  });

  // Ring 2: victims
  victims.forEach((t, i) => {
    nodes.push({
      id: t,
      label: shortLabel(t, 8),
      ring: 2,
      classification: "victim",
      delta: -(0.025 + i * 0.005),
    });
    if (ring1Source[0]) {
      edges.push({
        id: `e-${ring1Source[0]}-${t}`,
        from: ring1Source[0],
        to: t,
        weight: 0.55 - i * 0.04,
        hop: 2,
        label: "downstream pressure",
      });
    } else {
      edges.push({
        id: `e-shock-${t}`,
        from: shockId,
        to: t,
        weight: 0.55,
        hop: 2,
        label: "victim impact",
      });
    }
  });

  // Ring 3: indices
  indices.forEach((idx, i) => {
    const bias = (s.index_bias ?? {})[idx];
    nodes.push({
      id: idx,
      label: shortLabel(idx, 10),
      ring: 3,
      classification: bias === "BEARISH" ? "victim" : bias === "BULLISH" ? "beneficiary" : "neutral",
      delta: bias === "BEARISH" ? -0.018 : bias === "BULLISH" ? 0.014 : -0.005,
    });
    const upstream = victims[i % Math.max(1, victims.length)] ?? ring1Source[0];
    if (upstream) {
      edges.push({
        id: `e-${upstream}-${idx}`,
        from: upstream,
        to: idx,
        weight: 0.4,
        hop: 3,
        label: "index weight",
      });
    }
  });

  // Ring 4: multi-asset expression (FX, commodities, crypto, ETFs, rates)
  const multiAsset = [
    ...((s as any).fx_targets ?? []).slice(0, 2).map((t: string) => ({ id: `FX:${t}`, label: t, asset: "FX" })),
    ...((s as any).commodity_targets ?? []).slice(0, 2).map((t: string) => ({ id: `CMD:${t}`, label: t, asset: "COMMODITY" })),
    ...((s as any).crypto_targets ?? []).slice(0, 2).map((t: string) => ({ id: `CRY:${t}`, label: t, asset: "CRYPTO" })),
    ...((s as any).etf_targets ?? []).slice(0, 2).map((t: string) => ({ id: `ETF:${t}`, label: t, asset: "ETF" })),
    ...((s as any).rates_targets ?? []).slice(0, 2).map((t: string) => ({ id: `RATE:${t}`, label: t, asset: "RATE" })),
  ].slice(0, 6);

  multiAsset.forEach((ma, i) => {
    nodes.push({
      id: ma.id,
      label: `${ma.label} [${ma.asset}]`,
      ring: 4,
      classification: "beneficiary",
      delta: 0.01 + i * 0.003,
    });
    edges.push({
      id: `e-shock-${ma.id}`,
      from: shockId,
      to: ma.id,
      weight: 0.6 - i * 0.03,
      hop: 4,
      label: `${ma.asset.toLowerCase()} expression`,
    });
  });

  return { nodes, edges };
}

interface ScenarioWithIntel extends ShockScenario {}

function mapToSignals(real: ApiSignal[], scen?: ShockScenario): DemoSignal[] {
  if (real.length > 0) {
    return real.slice(0, 4).map((s, i) => ({
      id: `${s.ticker}-${i}`,
      emittedAt: s.computed_at,
      ticker: s.ticker,
      instrument: mapInstrument(s.instrument_type),
      direction: s.direction === "SHORT" ? "SHORT" : "LONG",
      strategy: s.strategy_name,
      conviction: s.conviction,
      expectedR: s.expected_r ?? 1.5,
      rationale: s.rationale ?? "",
      graphIntel: true,
    }));
  }
  // Fallback: synthesize from scenario beneficiaries so the demo still shows
  // graph-driven signals when the live signal book is empty.
  if (!scen) return [];
  const benes = scen.beneficiary_tickers ?? scen.tickers.slice(0, 3);
  // Anchor the synthetic emit times to the scenario's actual detected-at
  // and stagger 30..180s after so the pipeline latency reads like a real
  // graph walk + signal mint, not a row of identical "09:32:0X" stamps.
  const category: ShockEvent["category"] =
    (scen.shock_type ? SHOCK_TYPE_TO_CATEGORY[scen.shock_type.toLowerCase()] : undefined) ??
    "supply_disruption";
  const detectedAtISO = `${scen.start_date}T${_scenarioDetectedTime(scen.id, category)}Z`;
  return benes.slice(0, 4).map((t, i) => ({
    id: `synth-${t}`,
    emittedAt: _addSeconds(detectedAtISO, 30 + i * 35 + (_hash32(`${scen.id}-${t}`) % 20)),
    ticker: t,
    instrument: "EQUITY",
    direction: scen.expected_direction === "BEARISH" ? "SHORT" : "LONG",
    strategy: scen.shock_type ?? "shock_directed",
    conviction: 0.78 - i * 0.04,
    expectedR: 2.2 - i * 0.15,
    rationale: `Tier-${Math.min(3, i + 1)} graph beneficiary (${scen.shock_type ?? "shock"})`,
    graphIntel: true,
  }));
}

function mapInstrument(t: string): DemoSignal["instrument"] {
  const u = t?.toUpperCase?.() ?? "";
  if (u.includes("PUT")) return "INDEX_PUT";
  if (u.includes("CALL")) return "INDEX_CALL";
  if (u.includes("SPREAD")) return "INDEX_SPREAD";
  if (u.includes("FUTURE")) return "INDEX_FUTURE";
  if (u === "FX") return "EQUITY";
  if (u === "CRYPTO") return "EQUITY";
  if (u === "ETF") return "EQUITY";
  if (u === "COMMODITY_FUTURE") return "INDEX_FUTURE";
  if (u === "RATE_FUTURE") return "INDEX_FUTURE";
  return "EQUITY";
}

interface RawTrade {
  timestamp?: string;
  filled_at?: string;
  ticker?: string;
  symbol?: string;
  side?: string;
  action?: string;
  qty?: number;
  quantity?: number;
  price?: number;
  notional?: number;
  status?: string;
}

function mapToTrades(rows: unknown[]): DemoTrade[] {
  return (rows as RawTrade[]).slice(0, 6).map((r, i) => ({
    id: `${r.ticker ?? r.symbol ?? "t"}-${i}`,
    filledAt: r.timestamp ?? r.filled_at ?? new Date().toISOString(),
    ticker: r.ticker ?? r.symbol ?? "—",
    side: ((r.side ?? r.action ?? "BUY").toUpperCase().includes("SELL") ? "SELL" : "BUY") as DemoTrade["side"],
    qty: r.qty ?? r.quantity ?? 0,
    price: r.price ?? 0,
    notional: r.notional ?? (r.price ?? 0) * (r.qty ?? r.quantity ?? 0),
    status: ((r.status ?? "WORKING").toUpperCase() as DemoTrade["status"]),
  }));
}

function shortLabel(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
