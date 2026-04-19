"use client";

import { useEffect, useState } from "react";
import { ShockEventCard } from "@/components/ShockEvent";
import { GraphPropagation } from "@/components/GraphPropagation";
import { SignalsList } from "@/components/SignalsList";
import { TradesList } from "@/components/TradesList";
import {
  demoGraph,
  demoShock,
  demoSignals,
  demoTrades,
} from "@/lib/demoData";

const STAGES = [
  { id: 0, label: "Idle" },
  { id: 1, label: "Shock Detected" },
  { id: 2, label: "Graph Walk (3 hops)" },
  { id: 3, label: "Signals Generated" },
  { id: 4, label: "Orders Routed → IB Paper" },
] as const;

export default function LiveShockPage() {
  const [stage, setStage] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1500);
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

  return (
    <div className="space-y-5">
      <PipelineBar stage={stage} onReplay={replay} />

      <div className={`transition-opacity duration-500 ${stage >= 1 ? "opacity-100" : "opacity-30"}`}>
        <ShockEventCard event={demoShock} />
      </div>

      <div className={`transition-opacity duration-500 ${stage >= 2 ? "opacity-100" : "opacity-30"}`}>
        <GraphPropagation
          nodes={demoGraph.nodes}
          edges={demoGraph.edges}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className={`transition-opacity duration-500 ${stage >= 3 ? "opacity-100" : "opacity-30"}`}>
          <SignalsList signals={demoSignals} />
        </div>
        <div className={`transition-opacity duration-500 ${stage >= 4 ? "opacity-100" : "opacity-30"}`}>
          <TradesList trades={demoTrades} />
        </div>
      </div>
    </div>
  );
}

function PipelineBar({
  stage,
  onReplay,
}: {
  stage: number;
  onReplay: () => void;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
      <div className="flex items-center justify-between gap-4">
        <ol className="flex flex-1 items-center gap-2">
          {STAGES.slice(1).map((s, i) => {
            const idx = i + 1;
            const reached = stage >= idx;
            const active = stage === idx;
            return (
              <li
                key={s.id}
                className="flex flex-1 items-center gap-2 first:pl-0"
              >
                <div
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-mono transition-all ${
                    reached
                      ? "border-blue-400 bg-blue-500/20 text-blue-200"
                      : "border-white/10 bg-black/40 text-white/40"
                  } ${active ? "ring-2 ring-blue-400/40" : ""}`}
                >
                  {idx}
                </div>
                <span
                  className={`text-[11px] font-medium ${
                    reached ? "text-white/85" : "text-white/35"
                  }`}
                >
                  {s.label}
                </span>
                {idx < STAGES.length - 1 && (
                  <div
                    className={`h-px flex-1 ${
                      stage > idx
                        ? "bg-blue-400/60"
                        : "bg-white/10"
                    }`}
                  />
                )}
              </li>
            );
          })}
        </ol>
        <button
          onClick={onReplay}
          className="shrink-0 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-[11px] font-medium text-white/85 transition-colors hover:bg-white/10"
        >
          ▶ Replay Demo
        </button>
      </div>
    </div>
  );
}
