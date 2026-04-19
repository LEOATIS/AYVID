import type { ReactNode } from "react";
import clsx from "clsx";

export function Card({
  children,
  className,
  title,
  subtitle,
  right,
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  padded?: boolean;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
          <div className="min-w-0">
            {title && (
              <h3 className="text-sm font-semibold tracking-tight text-fg">{title}</h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-[12px] leading-relaxed text-muted">{subtitle}</p>
            )}
          </div>
          {right && <div className="flex shrink-0 items-center gap-2 text-xs text-muted">{right}</div>}
        </header>
      )}
      <div className={clsx(padded && "px-5 pb-5", title && padded && "pt-1")}>{children}</div>
    </section>
  );
}
