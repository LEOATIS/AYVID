import type { ShockEvent } from "@/lib/demoData";

const CATEGORY_LABEL: Record<ShockEvent["category"], string> = {
  supply_disruption: "Supply Disruption",
  tariff_escalation: "Tariff Escalation",
  rate_decision: "Rate Decision",
  geopolitical: "Geopolitical",
  earnings: "Earnings Surprise",
  macro_print: "Macro Print",
};

export function ShockEventCard({ event }: { event: ShockEvent }) {
  const negative = event.magnitude < 0;
  const dotBg = negative ? "bg-rose-400" : "bg-emerald-400";
  const dotText = negative ? "text-rose-300" : "text-emerald-300";
  const magnitudeText = negative ? "text-rose-300" : "text-emerald-300";
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-5 shadow-2xl shadow-black/40">
      <div className="flex flex-col items-start justify-between gap-6 lg:flex-row">
        <div>
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span
                className={`pulse-ring absolute inline-flex h-full w-full rounded-full opacity-80 ${dotBg}`}
              />
              <span
                className={`relative inline-flex h-2.5 w-2.5 rounded-full ${dotBg}`}
              />
            </span>
            <span
              className={`text-[11px] font-semibold uppercase tracking-[0.18em] ${dotText}`}
            >
              Live Shock Detected
            </span>
            <span className="text-[11px] text-white/40">
              · {CATEGORY_LABEL[event.category]}
            </span>
          </div>
          <h2 className="mt-2 max-w-3xl text-lg font-semibold leading-snug text-white/95">
            {event.headline}
          </h2>
          <p className="mt-2 text-xs text-white/55">{event.source}</p>
        </div>
        <div className="grid grid-cols-3 gap-3 text-right">
          <Metric
            label="Magnitude"
            value={`${(event.magnitude * 100).toFixed(2)}%`}
            valueClass={magnitudeText}
          />
          <Metric
            label="Confidence"
            value={`${(event.confidence * 100).toFixed(0)}%`}
            valueClass="text-cyan-300"
          />
          <Metric
            label="Detected"
            value={new Date(event.detectedAt).toUTCString().slice(17, 22)}
            valueClass="text-white/85"
          />
        </div>
      </div>
      <div className="mt-4 rounded-lg border border-white/5 bg-black/40 p-3 text-[12px] leading-relaxed text-white/65">
        <span className="text-[10px] uppercase tracking-[0.18em] text-white/40">
          Causal Chain (LLM-extracted)
        </span>
        <p className="mt-1 font-mono text-[11.5px] text-white/85">
          {event.causalChain}
        </p>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass: string;
}) {
  return (
    <div className="rounded-lg border border-white/5 bg-black/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-white/40">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-base font-semibold ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}
