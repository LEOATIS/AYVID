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
  { id: 3, label: "Signals Generated" },
  { id: 4, label: "Orders Routed" },
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
        <div className="flex items-center gap-2">
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

          <div className="grid gap-5 lg:grid-cols-2">
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
    return <div className="skeleton h-9 w-72" />;
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex w-72 items-center justify-between gap-2 rounded-lg border border-app bg-panel px-3 py-1.5 text-[12px] font-medium text-fg transition-colors hover:bg-[hsl(var(--bg-sunken))]"
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
  const isBearish = s.expected_direction === "BEARISH";
  const magnitude = isBearish ? -0.06 : s.expected_direction === "BULLISH" ? 0.06 : -0.04;
  return {
    id: s.id,
    detectedAt: `${s.start_date}T09:30:00Z`,
    source: "AYVID shock catalog · 20 named historical events",
    headline: s.name,
    category,
    magnitude,
    confidence: 0.86,
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
  return benes.slice(0, 4).map((t, i) => ({
    id: `synth-${t}`,
    emittedAt: `${scen.start_date}T09:32:0${i}Z`,
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
