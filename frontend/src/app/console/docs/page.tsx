import Link from "next/link";
import { ArrowUpRight, Code2, FileText, Plug, Sparkles, Terminal } from "lucide-react";
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
            description="LLM ingests headlines; LangGraph 7-agent debate; Neo4j 3-hop walk surfaces tickers."
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

      <Card
        title="Anthropic Financial Services integration"
        subtitle="AYVID = buy-side production of anthropic/financial-services"
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <IntegrationBlock
            label="Claude Skills"
            count="15 bundles"
            description="10 adapted from upstream equity-research + financial-analysis; 5 AYVID-native (bull/bear debate, shock thesis, causal validation, Kelly sizing, post-mortem). Markdown lives in .claude/skills/."
            icon={<Sparkles className="h-4 w-4" />}
          />
          <IntegrationBlock
            label="MCP connectors"
            count="5 servers"
            description="MT Newswires, Aiera, FactSet, Moody's, S&P Global. Wired into data_primitives/mcp/. Opt-in via MCP_*_URL env vars; falls back to OpenBB / SEC EDGAR / news scraper when unset."
            icon={<Plug className="h-4 w-4" />}
          />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <a
            href="https://github.com/anthropics/financial-services"
            target="_blank"
            rel="noopener noreferrer"
            className="pill pill-ghost !text-fg"
          >
            anthropics/financial-services <ArrowUpRight className="h-3 w-3" />
          </a>
          <span className="pill pill-ghost">document/yc/anthropic_integration.md</span>
          <span className="pill pill-ghost">shared/skills_loader.py</span>
        </div>
      </Card>

    </div>
  );
}

function IntegrationBlock({
  label,
  count,
  description,
  icon,
}: {
  label: string;
  count: string;
  description: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="card-soft p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-brand">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-[hsl(var(--brand)/0.12)]">
            {icon}
          </span>
          {label}
        </div>
        <span className="num text-[12px] text-subtle">{count}</span>
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-muted">{description}</p>
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
