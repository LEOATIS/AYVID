import { Cpu, Database, GitBranch, Globe2, Network, Radio, ShieldCheck, Workflow, Zap } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Continuous horizontal scroll of capability tags.  Renders the same
 * track twice so the loop is seamless.  CSS in `.marquee-track` does
 * the actual translation — no JS animation loop.
 */
const ITEMS: { icon: ReactNode; label: string }[] = [
  { icon: <Network className="h-3.5 w-3.5" />, label: "Causal supply-chain graph" },
  { icon: <Cpu className="h-3.5 w-3.5" />,     label: "LangGraph agent swarm" },
  { icon: <Database className="h-3.5 w-3.5" />, label: "Neo4j live topology" },
  { icon: <Workflow className="h-3.5 w-3.5" />, label: "End-to-end execution" },
  { icon: <Radio className="h-3.5 w-3.5" />,    label: "Real-time SEC + 10-K parse" },
  { icon: <Zap className="h-3.5 w-3.5" />,      label: "Sub-100ms tier-1 hedge" },
  { icon: <Globe2 className="h-3.5 w-3.5" />,   label: "20 global indices · 24/5" },
  { icon: <GitBranch className="h-3.5 w-3.5" />,label: "Walk-forward 2018→2025" },
  { icon: <ShieldCheck className="h-3.5 w-3.5" />, label: "C++ kill-switch watchdog" },
];

export function CapabilityMarquee() {
  const Track = (
    <div className="marquee-track" aria-hidden="false">
      {ITEMS.map((it) => (
        <span key={it.label} className="marquee-item">
          <span className="marquee-dot" aria-hidden />
          {it.icon}
          {it.label}
        </span>
      ))}
    </div>
  );
  return (
    <div className="marquee" role="list" aria-label="AYVID capabilities">
      {Track}
      {/* duplicated for seamless loop */}
      <div aria-hidden>{Track}</div>
    </div>
  );
}
