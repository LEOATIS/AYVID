import type { ReactNode } from "react";
import clsx from "clsx";

type Variant = "neutral" | "pos" | "neg" | "warn" | "info" | "brand";

const VARIANT: Record<Variant, string> = {
  neutral: "bg-[hsl(var(--bg-sunken))] text-muted ring-[hsl(var(--border))]",
  pos: "bg-[hsl(var(--pos)/0.12)] text-pos ring-[hsl(var(--pos)/0.3)]",
  neg: "bg-[hsl(var(--neg)/0.12)] text-neg ring-[hsl(var(--neg)/0.3)]",
  warn: "bg-[hsl(var(--warn)/0.12)] text-warn ring-[hsl(var(--warn)/0.3)]",
  info: "bg-[hsl(var(--info)/0.12)] text-[hsl(var(--info))] ring-[hsl(var(--info)/0.3)]",
  brand: "bg-[hsl(var(--brand)/0.12)] text-brand ring-[hsl(var(--brand)/0.3)]",
};

export function Badge({
  children,
  variant = "neutral",
  className,
}: {
  children: ReactNode;
  variant?: Variant;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        VARIANT[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
