import type { Trade } from "@/lib/demoData";

const STATUS_TINT: Record<Trade["status"], string> = {
  WORKING: "text-warn",
  FILLED: "text-pos",
  REJECTED: "text-neg",
};

export function TradesList({ trades }: { trades: Trade[] }) {
  return (
    <section className="card p-5">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-fg">
            Orders Routed (IB Paper)
          </h3>
          <p className="text-[11px] text-muted">
            Sized by Kelly · -1R hard stops · C++ kill-switch armed
          </p>
        </div>
        <span className="rounded-full bg-[hsl(var(--pos)/0.12)] px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-pos ring-1 ring-[hsl(var(--pos)/0.3)]">
          AUTO_EXECUTE
        </span>
      </header>
      <table className="w-full text-[12px]">
        <thead className="text-[10px] uppercase tracking-[0.14em] text-subtle">
          <tr className="border-b border-app">
            <th className="py-2 text-left font-medium">Ticker</th>
            <th className="py-2 text-left font-medium">Side</th>
            <th className="py-2 text-right font-medium">Qty</th>
            <th className="py-2 text-right font-medium">Px</th>
            <th className="py-2 text-right font-medium">Notional</th>
            <th className="py-2 text-right font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-app last:border-0">
              <td className="num py-2 text-fg">{t.ticker}</td>
              <td className="py-2 text-muted">{t.side}</td>
              <td className="num py-2 text-right text-fg">{t.qty}</td>
              <td className="num py-2 text-right text-fg">{t.price.toFixed(2)}</td>
              <td className="num py-2 text-right text-muted">${formatNotional(t.notional)}</td>
              <td className={`num py-2 text-right ${STATUS_TINT[t.status]}`}>
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
