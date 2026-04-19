import type { Trade } from "@/lib/demoData";

const STATUS_TINT: Record<Trade["status"], string> = {
  WORKING: "text-amber-300",
  FILLED: "text-emerald-300",
  REJECTED: "text-rose-300",
};

export function TradesList({ trades }: { trades: Trade[] }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white/90">
            Orders Routed (IB Paper)
          </h3>
          <p className="text-[11px] text-white/45">
            Sized by Kelly · -1R hard stops · C++ kill-switch armed
          </p>
        </div>
        <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-400/30">
          AUTO_EXECUTE
        </span>
      </header>
      <table className="w-full text-[12px]">
        <thead className="text-[10px] uppercase tracking-[0.14em] text-white/40">
          <tr className="border-b border-white/5">
            <th className="py-2 text-left font-normal">Ticker</th>
            <th className="py-2 text-left font-normal">Side</th>
            <th className="py-2 text-right font-normal">Qty</th>
            <th className="py-2 text-right font-normal">Px</th>
            <th className="py-2 text-right font-normal">Notional</th>
            <th className="py-2 text-right font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-white/5 last:border-0">
              <td className="py-2 font-mono text-white/95">{t.ticker}</td>
              <td className="py-2 text-white/70">{t.side}</td>
              <td className="py-2 text-right font-mono text-white/85">
                {t.qty}
              </td>
              <td className="py-2 text-right font-mono text-white/85">
                {t.price.toFixed(2)}
              </td>
              <td className="py-2 text-right font-mono text-white/70">
                ${formatNotional(t.notional)}
              </td>
              <td
                className={`py-2 text-right font-mono ${STATUS_TINT[t.status]}`}
              >
                {t.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function formatNotional(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
