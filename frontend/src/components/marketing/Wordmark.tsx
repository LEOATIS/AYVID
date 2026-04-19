import Link from "next/link";
import clsx from "clsx";

/**
 * AYVID brand mark — a serifed wordmark paired with a small acronym
 * expansion on demand.  Used in the marketing nav, marketing footer,
 * console nav, and anywhere else we need to identify the firm.
 *
 * Variants:
 *   compact (default): square monogram + "AYVID" wordmark (nav-sized)
 *   stacked          : monogram + wordmark + acronym tagline below
 *   inline           : just the wordmark (no monogram), for the footer
 */
export function Wordmark({
  variant = "compact",
  href = "/",
  showAcronym = true,
  monoBg = "ink",
  className,
}: {
  variant?: "compact" | "stacked" | "inline";
  href?: string | null;
  showAcronym?: boolean;
  monoBg?: "ink" | "brand";
  className?: string;
}) {
  const monoCls =
    monoBg === "brand"
      ? "bg-brand text-[hsl(var(--brand-fg))]"
      : "bg-ink text-[hsl(var(--bg))]";

  const inner = (
    <span className={clsx("group inline-flex items-center gap-3", className)}>
      {variant !== "inline" && (
        <span
          className={clsx(
            "grid place-items-center rounded-[3px] font-display leading-none transition-colors",
            monoCls,
            variant === "stacked" ? "h-10 w-10 text-[20px]" : "h-8 w-8 text-[16px]",
          )}
          aria-hidden
        >
          A
        </span>
      )}
      <span className="leading-none">
        <span
          className={clsx(
            "block font-display tracking-tight text-fg transition-colors group-hover:text-brand",
            variant === "stacked"
              ? "text-[22px] font-medium"
              : "text-[18px] font-medium",
          )}
        >
          AYVID
        </span>
        {showAcronym && variant === "stacked" && (
          <span className="mt-1.5 block text-[10px] font-semibold uppercase tracking-[0.22em] text-muted">
            <b className="text-fg">A</b>symmetric{" "}
            <b className="text-fg">Y</b>ield{" "}
            <b className="text-fg">V</b>ia{" "}
            <b className="text-fg">I</b>ntelligent{" "}
            <b className="text-fg">D</b>ecisions
          </span>
        )}
        {showAcronym && variant === "compact" && (
          <span className="mt-0.5 block text-[9px] font-semibold uppercase tracking-[0.22em] text-subtle">
            Asymmetric Yield · Intelligent Decisions
          </span>
        )}
      </span>
    </span>
  );

  return href ? (
    <Link href={href} className="inline-flex">
      {inner}
    </Link>
  ) : (
    inner
  );
}
