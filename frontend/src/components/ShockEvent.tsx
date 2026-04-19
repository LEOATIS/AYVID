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
    <section className="card p-5">
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
            <span className="text-[11px] text-subtle">
              · {CATEGORY_LABEL[event.category]}
            </span>
          </div>
          <h2 className="mt-2 max-w-3xl text-lg font-semibold leading-snug text-fg">
            {event.headline}
          </h2>
          <p className="mt-2 text-xs text-muted">{event.source}</p>
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
      <div className="mt-4 rounded-lg border border-app bg-[hsl(var(--bg-sunken))] p-3 text-[12px] leading-relaxed text-muted">
        <span className="text-[10px] uppercase tracking-[0.18em] text-subtle">
          Causal Chain (LLM-extracted)
        </span>
        <p className="num mt-1 text-[11.5px] text-fg">
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
    <div className="rounded-lg border border-app bg-[hsl(var(--bg-sunken))] px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-subtle">
        {label}
      </div>
      <div className={`num mt-0.5 text-base font-semibold ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}
