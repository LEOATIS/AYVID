import type { Signal } from "@/lib/demoData";

const INSTRUMENT_TINT: Record<Signal["instrument"], string> = {
  EQUITY: "bg-blue-500/15 text-blue-300 ring-blue-400/30",
  INDEX_PUT: "bg-rose-500/15 text-rose-300 ring-rose-400/30",
  INDEX_CALL: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  INDEX_SPREAD: "bg-purple-500/15 text-purple-300 ring-purple-400/30",
  INDEX_FUTURE: "bg-amber-500/15 text-amber-300 ring-amber-400/30",
  FX: "bg-cyan-500/15 text-cyan-300 ring-cyan-400/30",
  COMMODITY_FUTURE: "bg-orange-500/15 text-orange-300 ring-orange-400/30",
  CRYPTO: "bg-violet-500/15 text-violet-300 ring-violet-400/30",
  ETF: "bg-teal-500/15 text-teal-300 ring-teal-400/30",
  RATE_FUTURE: "bg-yellow-500/15 text-yellow-300 ring-yellow-400/30",
};

export function SignalsList({ signals }: { signals: Signal[] }) {
  return (
    <section className="card p-5">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-fg">
            Signals Generated
          </h3>
          <p className="text-[11px] text-muted">
            From the SignalBook · pre-computed every 15m, post-cost expected R
          </p>
        </div>
        <span className="rounded-full bg-[hsl(var(--brand)/0.12)] px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-brand ring-1 ring-[hsl(var(--brand)/0.3)]">
          {signals.length} live
        </span>
      </header>
      <ul className="divide-y divide-[hsl(var(--border))]">
        {signals.map((s) => (
          <li key={s.id} className="flex items-start gap-3 py-3">
            <div
              className={`mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium uppercase ring-1 ${INSTRUMENT_TINT[s.instrument]}`}
            >
              {s.instrument.replace("_", " ")}
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="num text-sm font-semibold text-fg">
                  {s.ticker}
                </span>
                <span className="text-[11px] text-muted">
                  · {s.direction}
                </span>
                {s.graphIntel && (
                  <span className="rounded-full bg-[hsl(var(--brand)/0.12)] px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-brand ring-1 ring-[hsl(var(--brand)/0.3)]">
                    Graph Intel
                  </span>
                )}
                <span className="text-[11px] text-subtle">
                  {s.strategy}
                </span>
              </div>
              <p className="mt-1 text-[12px] text-muted">{s.rationale}</p>
            </div>
            <div className="text-right text-[11px] text-muted">
              <div>
                conv{" "}
                <span className="num text-fg">
                  {s.conviction.toFixed(2)}
                </span>
              </div>
              <div>
                exp R{" "}
                <span
                  className={`num ${s.expectedR >= 1.5 ? "text-pos" : "text-warn"}`}
                >
                  {s.expectedR.toFixed(1)}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
