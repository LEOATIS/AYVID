import Link from "next/link";
import { ArrowUpRight, BookOpen, Code2, FileText, Terminal } from "lucide-react";
import { Card } from "@/components/ui/Card";

const SECTIONS: { title: string; description: string; links: { href: string; label: string }[] }[] = [
  {
    title: "Repository docs",
    description: "Markdown sources committed to the repo.",
    links: [
      { href: "/api/v1/live/system-status", label: "Live system status JSON" },
      { href: "https://github.com/", label: "Repository (GitHub)" },
    ],
  },
  {
    title: "Backend API",
    description:
      "Live-data endpoints exposed under /api/v1/live/* — no API key required for read-only diagnostics.",
    links: [
      { href: "/api/v1/live/equity", label: "GET /equity" },
      { href: "/api/v1/live/runner-status", label: "GET /runner-status" },
      { href: "/api/v1/live/signals", label: "GET /signals" },
      { href: "/api/v1/live/scenarios", label: "GET /scenarios" },
      { href: "/api/v1/live/backtests", label: "GET /backtests" },
      { href: "/api/v1/live/system-status", label: "GET /system-status" },
    ],
  },
  {
    title: "CLI",
    description:
      "AYVID is a Typer + Rich CLI. Run `ayvid` with no arguments for the splash, or `ayvid --help` for the command tree.",
    links: [
      { href: "/", label: "Open dashboard" },
      { href: "/console/live-shock", label: "60-sec demo screen" },
    ],
  },
];

export default function DocsPage() {
  return (
    <div className="flex flex-col gap-6 fade-up">
      <header>
        <h1 className="text-[26px] font-semibold tracking-tight text-fg">Reference</h1>
        <p className="mt-1 max-w-2xl text-[13px] text-muted">
          Quick links to the live API, repository docs, and the AYVID CLI.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {SECTIONS.map((s) => (
          <Card key={s.title} title={s.title} subtitle={s.description}>
            <ul className="space-y-1">
              {s.links.map((l) => (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    className="flex items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 text-[13px] text-fg hover:bg-[hsl(var(--bg-sunken))]"
                  >
                    <span className="num">{l.label}</span>
                    <ArrowUpRight className="h-3.5 w-3.5 text-muted" />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </section>

      <Card title="Architecture" subtitle="Tier-1 / Tier-2 / Tier-3 pipeline">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Tier
            tier="Tier 1"
            title="Sub-100ms execution"
            description="C++ kill-switch + IBKR FIX path. Reacts to pre-cached signals at order book tick speed."
            icon={<Terminal className="h-4 w-4" />}
          />
          <Tier
            tier="Tier 2"
            title="~30s shock override"
            description="LLM ingests headlines; LangGraph 4-agent debate; Neo4j 3-hop walk surfaces tickers."
            icon={<Code2 className="h-4 w-4" />}
          />
          <Tier
            tier="Tier 3"
            title="~5m signal book"
            description="Quant strategies re-priced every 15min. Walk-forward and shock-driven backtests stress-test the book."
            icon={<FileText className="h-4 w-4" />}
          />
        </div>
      </Card>

      <Card>
        <p className="flex items-start gap-3 text-[13px] text-muted">
          <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          <span>
            For the full architecture story, fund structure, and YC-S26 positioning,
            see <span className="num text-fg">document/yc/COMPANY_POSITIONING.md</span> and{" "}
            <span className="num text-fg">document/backtests/SHOCK_VS_NAIVE_COMPARISON.md</span>{" "}
            in the repository.
          </span>
        </p>
      </Card>
    </div>
  );
}

function Tier({ tier, title, description, icon }: { tier: string; title: string; description: string; icon: React.ReactNode }) {
  return (
    <div className="card-soft p-4">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-brand">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-[hsl(var(--brand)/0.12)]">
          {icon}
        </span>
        {tier}
      </div>
      <div className="mt-2 text-[13px] font-semibold text-fg">{title}</div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted">{description}</p>
    </div>
  );
}
