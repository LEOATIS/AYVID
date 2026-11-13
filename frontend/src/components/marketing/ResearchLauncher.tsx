"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { MARGIN_QUERY, OUTAGE_QUERY, PACKAGING_QUERY } from "@/lib/queries";

const SAMPLES = [OUTAGE_QUERY, PACKAGING_QUERY, MARGIN_QUERY];

/** Landing-page entry into the research workspace (internal access: the gate asks for the password). */
export function ResearchLauncher() {
  const router = useRouter();
  const [q, setQ] = useState(SAMPLES[0]);
  const go = (query: string) => router.push(`/research?q=${encodeURIComponent(query)}&run=1`);

  return (
    <div className="card-paper" style={{ padding: "var(--s-4)", borderRadius: 6 }}>
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) go(q.trim()); }}>
        <label htmlFor="launch-q" className="label-mono">Try a live research query</label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id="launch-q"
            name="query"
            autoComplete="off"
            className="input-paper min-w-0 flex-1"
            value={q}
            maxLength={1000}
            onChange={(e) => setQ(e.target.value)}
          />
          <button type="submit" className="btn-primary justify-center">
            Run research <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      </form>
      <ul className="mt-3 flex flex-col gap-1.5">
        {SAMPLES.map((s) => (
          <li key={s}>
            <button type="button" onClick={() => go(s)} className="text-left text-[13px] text-muted transition-colors hover:text-fg">
              → {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
