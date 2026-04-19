"use client";
import { useState } from "react";
import { Plus } from "lucide-react";

export type FaqItem = { q: string; a: string };

/**
 * FAQ accordion — Polo-style.  One item open at a time; first item
 * starts open so the section never looks empty.
 */
export function FaqAccordion({ items }: { items: FaqItem[] }) {
  const [open, setOpen] = useState<number>(0);

  return (
    <div role="list" className="border-t border-app">
      {items.map((it, i) => {
        const isOpen = open === i;
        return (
          <div
            key={it.q}
            role="listitem"
            className="faq-item"
            data-open={isOpen}
          >
            <button
              type="button"
              className="faq-trigger"
              onClick={() => setOpen(isOpen ? -1 : i)}
              aria-expanded={isOpen}
            >
              <span>{it.q}</span>
              <span className="faq-icon" aria-hidden>
                <Plus className="h-3.5 w-3.5" />
              </span>
            </button>
            <div className="faq-body">
              <div className="faq-body-inner text-[14.5px]">{it.a}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
