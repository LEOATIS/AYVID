import type { Signal } from "@/lib/demoData";

const INSTRUMENT_TINT: Record<Signal["instrument"], string> = {
  EQUITY: "bg-blue-500/15 text-blue-300 ring-blue-400/30",
  INDEX_PUT: "bg-rose-500/15 text-rose-300 ring-rose-400/30",
  INDEX_CALL: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  INDEX_SPREAD: "bg-purple-500/15 text-purple-300 ring-purple-400/30",
  INDEX_FUTURE: "bg-amber-500/15 text-amber-300 ring-amber-400/30",
};

export function SignalsList({ signals }: { signals: Signal[] }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white/90">
            Signals Generated
          </h3>
          <p className="text-[11px] text-white/45">
            From the SignalBook · pre-computed every 15m, post-cost expected R
          </p>
        </div>
        <span className="rounded-full bg-blue-500/10 px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-blue-300 ring-1 ring-blue-400/30">
          {signals.length} live
        </span>
      </header>
      <ul className="divide-y divide-white/5">
        {signals.map((s) => (
          <li key={s.id} className="flex items-start gap-3 py-3">
            <div
              className={`mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium uppercase ring-1 ${INSTRUMENT_TINT[s.instrument]}`}
            >
              {s.instrument.replace("_", " ")}
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-semibold text-white/95">
                  {s.ticker}
                </span>
                <span className="text-[11px] text-white/55">
                  · {s.direction}
                </span>
                {s.graphIntel && (
                  <span className="rounded-full bg-purple-500/15 px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-purple-300 ring-1 ring-purple-400/30">
                    Graph Intel
                  </span>
                )}
                <span className="text-[11px] text-white/40">
                  {s.strategy}
                </span>
              </div>
              <p className="mt-1 text-[12px] text-white/65">{s.rationale}</p>
            </div>
            <div className="text-right text-[11px] text-white/55">
              <div>
                conv{" "}
                <span className="font-mono text-white/85">
                  {s.conviction.toFixed(2)}
                </span>
              </div>
              <div>
                exp R{" "}
                <span
                  className={`font-mono ${s.expectedR >= 1.5 ? "text-emerald-300" : "text-amber-300"}`}
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
