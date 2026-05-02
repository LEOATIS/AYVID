import type { ReactNode } from "react";
import clsx from "clsx";

/**
 * StatTile — single-metric card.
 *
 * The value uses `.stat-value` (clamp + container query) so it scales with
 * the *card width*, not the viewport.  That way a tile inside a narrow
 * 2-up grid on a 700px tablet shrinks gracefully (e.g. $1,000,086) instead
 * of clipping the digits like the previous fixed `text-3xl` did.
 */
export function StatTile({
  label,
  value,
  delta,
  hint,
  trend = "neutral",
  icon,
  loading,
}: {
  label: ReactNode;
  value: ReactNode;
  delta?: ReactNode;
  hint?: ReactNode;
  trend?: "up" | "down" | "neutral";
  icon?: ReactNode;
  loading?: boolean;
}) {
  const trendCls =
    trend === "up" ? "text-pos" : trend === "down" ? "text-neg" : "text-muted";
  return (
    <div className="card stat-card flex min-w-0 flex-col justify-between p-4 sm:p-5 fade-up">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[10.5px] font-medium uppercase leading-snug tracking-[0.16em] text-muted sm:text-[11px]">
          {label}
        </span>
        {icon && <span className="shrink-0 text-muted">{icon}</span>}
      </div>
      <div className="mt-2.5 min-w-0">
        {loading ? (
          <>
            <div className="skeleton h-9 w-32" />
            {/* Hide delta + hint during load too — without this the
                StatTile flashes "+0% vs $0 start" underneath the value
                skeleton, which on the Portfolio page reads as a real
                "we're at zero" state until the first SWR resolve. */}
            {(delta || hint) && <div className="skeleton mt-1.5 h-3 w-24" />}
          </>
        ) : (
          <>
            <div className="num stat-value text-fg" title={typeof value === "string" ? value : undefined}>
              {value}
            </div>
            {(delta || hint) && (
              <div
                className={clsx(
                  "mt-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11.5px] sm:text-[12px]",
                  trendCls,
                )}
              >
                {delta && <span className="num font-medium">{delta}</span>}
                {hint && <span className="truncate text-muted">{hint}</span>}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
