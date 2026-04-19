"use client";

import { useEffect, useRef } from "react";

/**
 * HeroVisual — a depth-feeling causal-graph render for the marketing hero.
 *
 * No three.js, no WebGL.  We get a "3D" feel from three orthogonal tricks:
 *   1. SVG nodes laid out on three concentric perspective ellipses, with
 *      the outer ring scaled and dimmed (atmospheric perspective).
 *   2. A radial gradient backplate + a soft mesh-aurora layer beneath
 *      (defined in globals.css).
 *   3. Mouse-parallax: each ring drifts a fraction of the cursor delta,
 *      so the viewer's brain reads it as a 3D scene under glass.
 *
 * The graph also pulses on its own (CSS keyframes) so it feels alive
 * even when the visitor isn't moving the cursor.
 */
export function HeroVisual() {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const r1Ref = useRef<SVGGElement | null>(null);
  const r2Ref = useRef<SVGGElement | null>(null);
  const r3Ref = useRef<SVGGElement | null>(null);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    let raf = 0;
    let targetX = 0;
    let targetY = 0;
    let curX = 0;
    let curY = 0;

    const handleMove = (e: PointerEvent) => {
      const rect = svg.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      targetX = (e.clientX - cx) / rect.width;
      targetY = (e.clientY - cy) / rect.height;
    };

    const handleLeave = () => {
      targetX = 0;
      targetY = 0;
    };

    const tick = () => {
      curX += (targetX - curX) * 0.06;
      curY += (targetY - curY) * 0.06;
      const apply = (el: SVGGElement | null, depth: number) => {
        if (!el) return;
        el.style.transform = `translate3d(${curX * depth}px, ${curY * depth}px, 0)`;
      };
      apply(r1Ref.current, 18);
      apply(r2Ref.current, 10);
      apply(r3Ref.current, 4);
      raf = requestAnimationFrame(tick);
    };

    window.addEventListener("pointermove", handleMove, { passive: true });
    window.addEventListener("pointerleave", handleLeave);
    raf = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerleave", handleLeave);
      cancelAnimationFrame(raf);
    };
  }, []);

  // ── Node positions on three concentric "perspective" ellipses ──────
  // Outer ring (R1) is largest in number, smaller in radius weight,
  // dimmer; inner ring (R3) is the dense core.
  const R1 = ringNodes(8, 280, 220, 0);
  const R2 = ringNodes(6, 200, 160, Math.PI / 8);
  const R3 = ringNodes(5, 110, 86, Math.PI / 5);

  // Edges: connect inner core nodes to nearby mid nodes, mid nodes to
  // a few outer nodes — the "supply chain" fan-out.
  const edges = [
    ...connect(R3, R2, 2),
    ...connect(R2, R1, 2),
  ];

  return (
    <div className="relative h-full w-full">
      {/* Backdrop wash that gives the canvas depth */}
      <div className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(closest-side, hsl(var(--brand) / 0.18), transparent 70%)",
          }}
          aria-hidden
        />
      </div>

      <svg
        ref={svgRef}
        viewBox="-360 -300 720 600"
        className="relative h-full w-full"
        role="img"
        aria-label="A causal graph of the global economy — three concentric rings of nodes connected by edges, gently animated."
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <radialGradient id="nodeGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="hsl(var(--brand-2))" stopOpacity="1" />
            <stop offset="60%" stopColor="hsl(var(--brand))" stopOpacity="0.85" />
            <stop offset="100%" stopColor="hsl(var(--brand))" stopOpacity="0.3" />
          </radialGradient>
          <radialGradient id="coreGrad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="hsl(var(--brand-2))" stopOpacity="1" />
            <stop offset="100%" stopColor="hsl(var(--brand))" stopOpacity="0.9" />
          </radialGradient>
          <linearGradient id="edgeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="hsl(var(--brand) / 0)" />
            <stop offset="50%" stopColor="hsl(var(--brand) / 0.55)" />
            <stop offset="100%" stopColor="hsl(var(--brand) / 0)" />
          </linearGradient>
          <filter id="softGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Faint "horizon" rings hint at depth */}
        <g opacity="0.08" stroke="hsl(var(--fg))" fill="none">
          <ellipse cx="0" cy="0" rx="280" ry="220" />
          <ellipse cx="0" cy="0" rx="200" ry="160" />
          <ellipse cx="0" cy="0" rx="110" ry="86" />
        </g>

        {/* Edges — drawn first so nodes sit on top */}
        <g stroke="url(#edgeGrad)" strokeWidth="1.1" fill="none" opacity="0.7">
          {edges.map((e, i) => (
            <line
              key={`e-${i}`}
              x1={e.from.x}
              y1={e.from.y}
              x2={e.to.x}
              y2={e.to.y}
              strokeDasharray="2 6"
              className="edge-flow"
              style={{ animationDelay: `${(i % 7) * 0.18}s` }}
            />
          ))}
        </g>

        {/* Outer ring — atmospheric */}
        <g ref={r1Ref} opacity="0.72">
          {R1.map((n, i) => (
            <g key={`r1-${i}`} transform={`translate(${n.x} ${n.y})`}>
              <circle r="3.6" fill="url(#nodeGrad)" />
              <circle
                r="3.6"
                fill="hsl(var(--brand))"
                opacity="0.6"
                style={{
                  animation: `node-ping 3.8s ease-in-out ${i * 0.22}s infinite`,
                }}
              />
            </g>
          ))}
        </g>

        {/* Mid ring */}
        <g ref={r2Ref}>
          {R2.map((n, i) => (
            <g key={`r2-${i}`} transform={`translate(${n.x} ${n.y})`}>
              <circle r="5" fill="url(#nodeGrad)" filter="url(#softGlow)" />
              <circle
                r="5"
                fill="hsl(var(--brand-2))"
                opacity="0.7"
                style={{
                  animation: `node-ping 2.6s ease-in-out ${i * 0.31}s infinite`,
                }}
              />
            </g>
          ))}
        </g>

        {/* Inner core ring — densest, brightest */}
        <g ref={r3Ref}>
          {R3.map((n, i) => (
            <g key={`r3-${i}`} transform={`translate(${n.x} ${n.y})`}>
              <circle r="6.5" fill="url(#coreGrad)" filter="url(#softGlow)" />
              <circle
                r="6.5"
                fill="hsl(var(--brand-2))"
                opacity="0.85"
                style={{
                  animation: `node-ping 2s ease-in-out ${i * 0.18}s infinite`,
                }}
              />
            </g>
          ))}

          {/* Center "shock" pulse — the source */}
          <g>
            <circle r="9" fill="hsl(var(--brand-2))" filter="url(#softGlow)" />
            <circle r="9" fill="none" stroke="hsl(var(--brand-2))" strokeWidth="1.2" opacity="0.6">
              <animate attributeName="r" from="9" to="42" dur="2.4s" repeatCount="indefinite" />
              <animate attributeName="opacity" from="0.7" to="0" dur="2.4s" repeatCount="indefinite" />
            </circle>
            <circle r="9" fill="none" stroke="hsl(var(--brand-2))" strokeWidth="1.2" opacity="0.6">
              <animate
                attributeName="r"
                from="9"
                to="42"
                dur="2.4s"
                begin="1.2s"
                repeatCount="indefinite"
              />
              <animate
                attributeName="opacity"
                from="0.7"
                to="0"
                dur="2.4s"
                begin="1.2s"
                repeatCount="indefinite"
              />
            </circle>
          </g>
        </g>
      </svg>
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────────
type N = { x: number; y: number };

function ringNodes(count: number, rx: number, ry: number, phase = 0): N[] {
  const out: N[] = [];
  for (let i = 0; i < count; i++) {
    const t = phase + (i / count) * Math.PI * 2;
    out.push({ x: Math.cos(t) * rx, y: Math.sin(t) * ry });
  }
  return out;
}

function connect(inner: N[], outer: N[], k: number) {
  const edges: { from: N; to: N }[] = [];
  for (const a of inner) {
    // pick the k nearest outer nodes
    const sorted = outer
      .map((b) => ({ b, d: (a.x - b.x) ** 2 + (a.y - b.y) ** 2 }))
      .sort((u, v) => u.d - v.d)
      .slice(0, k);
    for (const { b } of sorted) edges.push({ from: a, to: b });
  }
  return edges;
}
