"use client";
import { useMemo, useState } from "react";
import { Network, Search } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { useScenarios } from "@/lib/hooks";
import { fmtDate } from "@/lib/format";
import type { ShockScenario } from "@/lib/api";

export default function ScenariosPage() {
  const { data, isLoading } = useScenarios();
  const all: ShockScenario[] = data?.scenarios ?? [];
  const [q, setQ] = useState("");
  const [dir, setDir] = useState<"all" | "BULLISH" | "BEARISH">("all");

  const filtered = useMemo(() => {
    return all.filter((s) => {
      if (dir !== "all" && s.expected_direction !== dir) return false;
      if (!q) return true;
      const needle = q.toLowerCase();
      return (
        s.name.toLowerCase().includes(needle) ||
        s.description?.toLowerCase().includes(needle) ||
        s.tickers.some((t) => t.toLowerCase().includes(needle)) ||
        s.shock_type?.toLowerCase().includes(needle)
      );
    });
  }, [all, q, dir]);

  return (
    <div className="flex flex-col gap-6 fade-up">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight text-fg">Shock catalog</h1>
          <p className="mt-1 max-w-2xl text-[13px] text-muted">
            The {all.length || ""} named historical shocks the engine trades against — each
            with the causal chain, beneficiaries, and victims surfaced by the Neo4j graph.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-app bg-panel px-3 py-1.5">
            <Search className="h-3.5 w-3.5 text-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search scenario, ticker, type…"
              className="w-56 bg-transparent text-[13px] text-fg outline-none placeholder:text-subtle"
            />
          </div>
          <div className="flex items-center gap-0.5 rounded-lg border border-app bg-panel p-0.5">
            {(["all", "BULLISH", "BEARISH"] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDir(d)}
                className={`rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors ${
                  dir === d
                    ? "bg-[hsl(var(--bg-sunken))] text-fg"
                    : "text-muted hover:text-fg"
                }`}
              >
                {d === "all" ? "All" : d}
              </button>
            ))}
          </div>
        </div>
      </header>

      {isLoading && <div className="skeleton h-32" />}
      {!isLoading && filtered.length === 0 && (
        <Card>
          <Empty icon={<Network className="h-8 w-8" />} title="No matches" />
        </Card>
      )}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((s) => (
          <article id={s.id} key={s.id} className="card flex flex-col gap-3 p-5 fade-up">
            <header className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[14px] font-semibold tracking-tight text-fg">{s.name}</div>
                <div className="num mt-0.5 text-[11px] text-muted">
                  {fmtDate(s.start_date)} → {fmtDate(s.end_date)}
                </div>
              </div>
              <Badge
                variant={
                  s.expected_direction === "BEARISH"
                    ? "neg"
                    : s.expected_direction === "BULLISH"
                    ? "pos"
                    : "info"
                }
              >
                {s.expected_direction ?? "shock"}
              </Badge>
            </header>

            <p className="text-[12px] leading-relaxed text-muted">{s.description}</p>

            {s.causal_chain && (
              <p className="rounded-md border border-app bg-[hsl(var(--bg-sunken))] p-2.5 text-[11.5px] leading-relaxed text-fg">
                <span className="text-[10px] uppercase tracking-wider text-subtle">causal chain</span>
                <br />
                {s.causal_chain}
              </p>
            )}

            <div className="flex flex-wrap gap-1.5">
              <span className="text-[10px] uppercase tracking-wider text-subtle">tickers</span>
              {s.tickers.slice(0, 8).map((t) => (
                <Badge key={t} variant="neutral">
                  <span className="num">{t}</span>
                </Badge>
              ))}
              {s.tickers.length > 8 && (
                <Badge variant="neutral">+{s.tickers.length - 8}</Badge>
              )}
            </div>

            {(s.beneficiary_tickers?.length || s.victim_tickers?.length) && (
              <div className="grid grid-cols-2 gap-2 border-t border-app pt-2.5">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-subtle">Beneficiaries</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(s.beneficiary_tickers ?? []).slice(0, 6).map((t) => (
                      <Badge key={t} variant="pos"><span className="num">{t}</span></Badge>
                    ))}
                    {(s.beneficiary_tickers?.length ?? 0) === 0 && (
                      <span className="text-[11px] text-subtle">—</span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-subtle">Victims</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(s.victim_tickers ?? []).slice(0, 6).map((t) => (
                      <Badge key={t} variant="neg"><span className="num">{t}</span></Badge>
                    ))}
                    {(s.victim_tickers?.length ?? 0) === 0 && (
                      <span className="text-[11px] text-subtle">—</span>
                    )}
                  </div>
                </div>
              </div>
            )}
          </article>
        ))}
      </section>
    </div>
  );
}
