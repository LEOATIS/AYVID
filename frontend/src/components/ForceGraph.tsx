"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type EdgeProps,
  BaseEdge,
  getBezierPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import type { GraphEdge, GraphNode } from "@/lib/demoData";

/**
 * ForceGraph — the force-directed supply-chain viewer, rewritten on
 * React Flow. Replaces the canvas-based CytoscapeSupplyChain that
 * suffered from unavoidable label rendering bugs when it tried to
 * consume CSS custom-property theme tokens.
 *
 * Why this works where Cytoscape didn't
 * ──────────────────────────────────────
 * React Flow renders *edges* as SVG and *nodes* as regular DOM
 * elements. That means every node is a standard React component,
 * free to use Tailwind classes, CSS variables, backdrop-blur, the
 * full design-system theme — nothing gets flattened by a canvas
 * path. Motion (the travelling dashed pulse, the shock halo) is
 * animated via CSS keyframes on SVG edges, which is native and GPU
 * accelerated.
 *
 * Layout
 * ──────
 * Positions are computed once at mount via a short d3-force
 * simulation — radial pull per ring (concentric onion), link
 * elasticity, charge repulsion, collision avoidance — then handed
 * to React Flow as static positions. Users can still drag nodes;
 * React Flow handles that for free.
 */

type Props = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** 0/undefined = idle. 1/2/3 highlights the matching hop. */
  activeHop?: number;
  className?: string;
};

type Classification = GraphNode["classification"];
type NodeDatum = {
  label: string;
  classification: Classification;
  ring: GraphNode["ring"];
  delta: number;
  activeHop: number;
};
type EdgeDatum = {
  label?: string;
  weight: number;
  hop: number;
  activeHop: number;
};

// Palette — Stripe-grade, institutional. Each classification has a
// saturated ink and a matching glow tone for the halo + shadow.
const PALETTE: Record<Classification, {
  ink: string; border: string; glow: string; text: string;
}> = {
  shock:       { ink: "#E11D48", border: "#881337", glow: "#FB7185", text: "#FFFFFF" },
  beneficiary: { ink: "#059669", border: "#064E3B", glow: "#34D399", text: "#FFFFFF" },
  victim:      { ink: "#D97706", border: "#78350F", glow: "#FBBF24", text: "#FFFFFF" },
  neutral:     { ink: "#4F46E5", border: "#312E81", glow: "#818CF8", text: "#FFFFFF" },
};

const HOP_COLOR: Record<1 | 2 | 3, string> = {
  1: "#E11D48",
  2: "#D97706",
  3: "#4F46E5",
};

// ────────────────────────────────────────────────────────────────
// Custom node — the premium chip
// ────────────────────────────────────────────────────────────────

function ShockNode({ data }: NodeProps<Node<NodeDatum>>) {
  const p = PALETTE.shock;
  const isIdle = (data.activeHop ?? 0) === 0;
  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: 80, height: 80 }}
    >
      {/* Anchor the edge-handle at the centre for symmetric radii */}
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />

      {/* Outer pulse ring — always on */}
      <span
        aria-hidden
        className="absolute inset-0 rounded-full"
        style={{
          background: p.glow,
          animation: "ayv-shock-pulse 1.8s ease-out infinite",
        }}
      />
      {/* Soft halo behind the diamond */}
      <span
        aria-hidden
        className="absolute inset-1 rounded-full"
        style={{ background: p.glow, opacity: 0.42, filter: "blur(6px)" }}
      />
      {/* The diamond itself */}
      <div
        className="relative flex items-center justify-center"
        style={{
          width: 54, height: 54,
          transform: "rotate(45deg)",
          background: `linear-gradient(135deg, ${p.glow} 0%, ${p.ink} 60%, ${p.border} 100%)`,
          border: `1.5px solid ${p.border}`,
          borderRadius: 10,
          boxShadow:
            "0 6px 14px hsl(0 0% 0% / 0.15), inset 0 1px 0 hsl(0 0% 100% / 0.25)",
        }}
      >
        <span
          className="text-[10px] font-bold tracking-widest"
          style={{ transform: "rotate(-45deg)", color: p.text }}
        >
          SHOCK
        </span>
      </div>
      {/* Label caption BELOW the node, outside the shape */}
      <div
        className="pointer-events-none absolute left-1/2 flex flex-col items-center gap-0.5"
        style={{ top: "100%", transform: "translate(-50%, 6px)" }}
      >
        <span
          className="whitespace-nowrap rounded-md border px-1.5 py-[1px] text-[11px] font-semibold"
          style={{
            borderColor: "hsl(var(--border))",
            background: "hsl(var(--bg-elev))",
            color: "hsl(var(--fg))",
          }}
        >
          {data.label}
        </span>
        <DeltaBadge value={data.delta} />
      </div>
      {/* Idle hint: don't dim the shock */}
      {isIdle ? null : null}
    </div>
  );
}

function ChipNode({ data }: NodeProps<Node<NodeDatum>>) {
  const p = PALETTE[data.classification];
  const size = data.classification === "beneficiary" ? 44
              : data.classification === "victim" ? 42
              : 40;

  // Shape variant per classification — all with a common card skin so
  // the visual rhythm is consistent.
  const shapeStyle =
    data.classification === "beneficiary"
      ? { borderRadius: 8 }  // rounded-rectangle
      : data.classification === "victim"
      ? { borderRadius: "50% 50% 50% 8px" } // asymmetric tag
      : { borderRadius: "50%" };            // circle

  const dim = data.activeHop > 0 && data.activeHop !== data.ring;

  return (
    <div
      className="relative flex items-center justify-center transition-opacity duration-300"
      style={{
        width: size, height: size,
        opacity: dim ? 0.34 : 1,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />

      {/* Active-ring glow — only when this node's ring matches activeHop */}
      {!dim && data.activeHop > 0 && data.activeHop === data.ring ? (
        <span
          aria-hidden
          className="absolute -inset-2 transition-opacity"
          style={{
            ...shapeStyle,
            background: p.glow,
            opacity: 0.35,
            filter: "blur(8px)",
            animation: "ayv-ring-breathe 2.4s ease-in-out infinite",
          }}
        />
      ) : null}

      {/* Chip body — solid fill with subtle gradient + specular */}
      <div
        className="relative flex items-center justify-center"
        style={{
          width: size, height: size,
          ...shapeStyle,
          background: `linear-gradient(140deg, ${p.glow} 0%, ${p.ink} 55%, ${p.border} 100%)`,
          border: `1.5px solid ${p.border}`,
          boxShadow:
            "0 4px 10px hsl(0 0% 0% / 0.12), inset 0 1px 0 hsl(0 0% 100% / 0.22), inset 0 -1px 0 hsl(0 0% 0% / 0.1)",
        }}
      />

      {/* Label + delta stacked BELOW the chip, properly outside its bounds */}
      <div
        className="pointer-events-none absolute left-1/2 flex flex-col items-center gap-0.5"
        style={{ top: "100%", transform: "translate(-50%, 6px)" }}
      >
        <span
          className="whitespace-nowrap rounded-md border px-1.5 py-[1px] text-[10.5px] font-semibold"
          style={{
            borderColor: "hsl(var(--border))",
            background: "hsl(var(--bg-elev))",
            color: "hsl(var(--fg))",
          }}
        >
          {data.label}
        </span>
        <DeltaBadge value={data.delta} />
      </div>
    </div>
  );
}

function DeltaBadge({ value }: { value: number }) {
  const pct = `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
  const pos = value >= 0;
  return (
    <span
      className="font-mono text-[10px] font-semibold tabular-nums"
      style={{ color: pos ? "#10B981" : "#E11D48" }}
    >
      {pct}
    </span>
  );
}

// ────────────────────────────────────────────────────────────────
// Custom edge — active hop gets the travelling dash animation
// ────────────────────────────────────────────────────────────────

function HopEdge({
  id,
  sourceX, sourceY,
  targetX, targetY,
  sourcePosition, targetPosition,
  data,
  markerEnd,
}: EdgeProps<Edge<EdgeDatum>>) {
  const d = data ?? ({ hop: 1, weight: 0.5, activeHop: 0 } as EdgeDatum);
  const active = d.activeHop > 0 && d.activeHop === d.hop;
  const dim = d.activeHop > 0 && !active;
  const hopColor = HOP_COLOR[d.hop as 1 | 2 | 3] ?? HOP_COLOR[3];

  const [pathD, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
  });

  const baseOpacity = dim ? 0.18 : active ? 1 : 0.55;
  const stroke = active ? hopColor : dim ? "hsl(var(--border-strong))" : hopColor;
  const width = active ? 2.4 : 1.3 + (d.weight ?? 0.5) * 0.6;

  return (
    <>
      <BaseEdge
        id={id}
        path={pathD}
        markerEnd={markerEnd}
        style={{
          stroke,
          strokeWidth: width,
          opacity: baseOpacity,
          strokeDasharray: active ? "5 4" : undefined,
          animation: active ? "ayv-edge-flow 1.1s linear infinite" : undefined,
          transition: "opacity 260ms, stroke 260ms, stroke-width 260ms",
        }}
      />
      {active && d.label ? (
        <foreignObject
          width={120}
          height={18}
          x={labelX - 60}
          y={labelY - 9}
          className="pointer-events-none"
        >
          <div
            className="flex h-full items-center justify-center"
            style={{
              fontFamily: "Inter, system-ui, sans-serif",
              fontSize: 9.5,
              fontWeight: 500,
            }}
          >
            <span
              className="whitespace-nowrap rounded-full border px-2 py-[1px]"
              style={{
                borderColor: hopColor,
                background: "hsl(var(--bg-elev))",
                color: "hsl(var(--fg))",
                boxShadow: `0 0 6px ${hopColor}33`,
              }}
            >
              {d.label}
            </span>
          </div>
        </foreignObject>
      ) : null}
    </>
  );
}

const NODE_TYPES = {
  shock: ShockNode,
  chip: ChipNode,
};

const EDGE_TYPES = {
  hop: HopEdge,
};

// ────────────────────────────────────────────────────────────────
// Layout — d3-force simulation, single-shot at mount
// ────────────────────────────────────────────────────────────────

type PositionedInput = GraphNode & { x: number; y: number };

function simulatePositions(
  nodes: GraphNode[], edges: GraphEdge[],
): PositionedInput[] {
  type SimNode = SimulationNodeDatum & GraphNode;
  type SimLink = SimulationLinkDatum<SimNode> & { hop: number };

  const simNodes: SimNode[] = nodes.map((n) => ({ ...n }));
  const simLinks: SimLink[] = edges.map((e) => ({
    source: e.from, target: e.to, hop: e.hop,
  }));

  // Concentric target radii per ring; shock anchored to origin.
  const ringRadius: Record<number, number> = { 0: 0, 1: 170, 2: 300, 3: 420, 4: 520 };

  const sim = forceSimulation<SimNode>(simNodes)
    .force("link",
      forceLink<SimNode, SimLink>(simLinks)
        .id((d) => d.id)
        .distance((l) => 110 + (l.hop - 1) * 50)
        .strength(0.22))
    .force("charge", forceManyBody<SimNode>().strength(-480))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide<SimNode>().radius(56).strength(0.9))
    .force(
      "radial",
      forceRadial<SimNode>(
        (d) => ringRadius[d.ring] ?? 420,
        0, 0,
      ).strength(0.92),
    )
    .stop();

  for (let i = 0; i < 320; i++) sim.tick();

  // Pin the shock at dead centre so the fan layout reads correctly.
  return simNodes.map((n) => ({
    ...n,
    x: n.ring === 0 ? 0 : (n.x ?? 0),
    y: n.ring === 0 ? 0 : (n.y ?? 0),
  }));
}

// ────────────────────────────────────────────────────────────────
// Main component
// ────────────────────────────────────────────────────────────────

function ForceGraphInner({ nodes, edges, activeHop = 0 }: Props) {
  const [stats] = useState({
    nodes: nodes.length, edges: edges.length,
  });

  const rfNodes: Node<NodeDatum>[] = useMemo(() => {
    const positioned = simulatePositions(nodes, edges);
    return positioned.map((n) => ({
      id: n.id,
      type: n.classification === "shock" ? "shock" : "chip",
      position: { x: n.x, y: n.y },
      data: {
        label: n.label,
        classification: n.classification,
        ring: n.ring,
        delta: n.delta,
        activeHop,
      },
      draggable: true,
      selectable: false,
    }));
    // Only re-simulate when the topology changes. `activeHop` is
    // propagated through a separate effect below so we don't re-run
    // the force layout on every pipeline tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  const rfEdges: Edge<EdgeDatum>[] = useMemo(() => edges.map((e) => ({
    id: e.id,
    source: e.from,
    target: e.to,
    type: "hop",
    data: {
      label: e.label,
      weight: e.weight,
      hop: e.hop,
      activeHop,
    },
  })), [edges, activeHop]);

  // Stream activeHop into each node's data without re-simulating.
  const liveNodes = useMemo(
    () => rfNodes.map((n) => ({ ...n, data: { ...n.data, activeHop } })),
    [rfNodes, activeHop],
  );

  return (
    <ReactFlow
      nodes={liveNodes}
      edges={rfEdges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      proOptions={{ hideAttribution: true }}
      minZoom={0.4}
      maxZoom={2.8}
      fitView
      fitViewOptions={{ padding: 0.18, includeHiddenNodes: false }}
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag
      zoomOnScroll
      zoomOnPinch
      panOnScroll={false}
      className="[&_.react-flow__attribution]:hidden"
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={22}
        size={1.2}
        color="hsl(var(--border))"
      />
      <Controls
        showInteractive={false}
        className="[&_button]:!border-app [&_button]:!bg-[hsl(var(--bg-elev)/0.95)] [&_button]:!text-fg [&_button:hover]:!bg-[hsl(var(--bg-sunken))]"
      />
      <ActiveHopBadge activeHop={activeHop} stats={stats} />
    </ReactFlow>
  );
}

function ActiveHopBadge({
  activeHop, stats,
}: { activeHop: number; stats: { nodes: number; edges: number } }) {
  return (
    <div
      className="pointer-events-none absolute right-3 top-3 z-[5] flex items-center gap-2 rounded-full border px-2.5 py-1 text-[10.5px] font-medium text-fg backdrop-blur-md"
      style={{
        borderColor: "hsl(var(--border))",
        background: "hsl(var(--bg-elev) / 0.85)",
      }}
    >
      <span className="num text-muted">
        {stats.nodes} nodes · {stats.edges} edges
      </span>
      {activeHop > 0 ? (
        <>
          <span className="text-subtle">·</span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{
                background: HOP_COLOR[activeHop as 1 | 2 | 3] ?? HOP_COLOR[3],
                boxShadow: `0 0 8px ${HOP_COLOR[activeHop as 1 | 2 | 3] ?? HOP_COLOR[3]}`,
                animation: "ayv-dot-pulse 1.2s ease-in-out infinite",
              }}
            />
            propagating · hop {activeHop}
          </span>
        </>
      ) : null}
    </div>
  );
}

export function ForceGraph({ nodes, edges, activeHop, className }: Props) {
  useKeyframesOnce();
  return (
    <section className={`card relative overflow-hidden p-0 ${className ?? ""}`}>
      <header className="flex flex-col gap-2 border-b border-app px-5 py-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 className="font-display text-[18px] font-medium tracking-tight text-fg">
            Supply-chain topology · force-directed
          </h3>
          <p className="mt-0.5 text-[12px] text-muted">
            d3-force layout · drag / pan / zoom · SVG-native rendering
          </p>
        </div>
      </header>

      <div className="relative h-[520px] w-full">
        {/* A whisper of brand-coloured light above the dots — gives the
            graph a stage without stealing focus from the nodes. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-[1]"
          style={{
            background:
              "radial-gradient(55% 65% at 50% 45%, hsl(var(--brand) / 0.06) 0%, transparent 75%)",
          }}
        />
        <ReactFlowProvider>
          <ForceGraphInner
            nodes={nodes}
            edges={edges}
            activeHop={activeHop}
          />
        </ReactFlowProvider>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-app bg-[hsl(var(--bg-elev))] px-5 py-3 text-[11px] text-muted">
        <LegendDot color={PALETTE.shock.ink}       glow={PALETTE.shock.glow}       label="Shock"        shape="diamond" />
        <LegendDot color={PALETTE.beneficiary.ink} glow={PALETTE.beneficiary.glow} label="Beneficiary"  shape="rounded" />
        <LegendDot color={PALETTE.victim.ink}      glow={PALETTE.victim.glow}      label="Victim"       shape="tag" />
        <LegendDot color={PALETTE.neutral.ink}     glow={PALETTE.neutral.glow}     label="Neutral"      shape="circle" />
        <span className="ml-auto flex items-center gap-3">
          <HopChip color={HOP_COLOR[1]} n={1} />
          <HopChip color={HOP_COLOR[2]} n={2} />
          <HopChip color={HOP_COLOR[3]} n={3} />
        </span>
      </div>
    </section>
  );
}

function LegendDot({
  color, glow, label, shape,
}: {
  color: string; glow: string; label: string;
  shape: "diamond" | "rounded" | "tag" | "circle";
}) {
  const borderRadius =
    shape === "diamond" ? 2
      : shape === "rounded" ? 4
      : shape === "tag"     ? "50% 50% 50% 3px"
      : 999;
  const transform = shape === "diamond" ? "rotate(45deg)" : undefined;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block h-2.5 w-2.5 border"
        style={{
          background: color, borderColor: color,
          borderRadius, transform,
          boxShadow: `0 0 6px ${glow}`,
        }}
      />
      {label}
    </span>
  );
}

function HopChip({ color, n }: { color: string; n: number }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block h-[2.5px] w-5 rounded-full"
        style={{ background: color, boxShadow: `0 0 5px ${color}` }}
      />
      Hop {n}
    </span>
  );
}

// Hoist keyframes once into the document on first mount. Keeps the
// component fully self-contained — no globals.css edits required to
// make motion work. SSR-safe because it runs inside useEffect.
function useKeyframesOnce() {
  useEffect(() => {
    if (typeof document === "undefined") return;
    const KEY = "ayv-force-graph-keyframes";
    if (document.getElementById(KEY)) return;
    const style = document.createElement("style");
    style.id = KEY;
    style.textContent = `
@keyframes ayv-shock-pulse {
  0%   { transform: scale(0.6); opacity: 0.9; }
  80%  { transform: scale(2.0); opacity: 0; }
  100% { transform: scale(2.0); opacity: 0; }
}
@keyframes ayv-ring-breathe {
  0%, 100% { transform: scale(1);    opacity: 0.35; }
  50%      { transform: scale(1.06); opacity: 0.55; }
}
@keyframes ayv-edge-flow {
  to { stroke-dashoffset: -20; }
}
@keyframes ayv-dot-pulse {
  0%, 100% { transform: scale(1);    opacity: 1; }
  50%      { transform: scale(1.35); opacity: 0.6; }
}
    `;
    document.head.appendChild(style);
  }, []);
}
