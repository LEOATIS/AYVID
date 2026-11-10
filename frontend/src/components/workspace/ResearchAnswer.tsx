"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useState } from "react";
import type { ReactNode } from "react";
import clsx from "clsx";
import { useSWRConfig } from "swr";
import { researchApi, useTheses, type Artifact, type Claim, type DataRow, type Exposure } from "@/lib/research";
import { useEvidence } from "./EvidenceDrawer";
import { ExposureGraph } from "./ExposureGraph";

const EPI_MARK: Record<Claim["epistemic"], string> = {
  REPORTED: "RPT",
  CALCULATED: "CALC",
  MANAGEMENT: "MGMT",
  INTERPRETATION: "INTERP",
  HYPOTHESIS: "HYP",
};
const EPI_TITLE: Record<Claim["epistemic"], string> = {
  REPORTED: "Reported fact",
  CALCULATED: "Deterministic calculation",
  MANAGEMENT: "Management statement",
  INTERPRETATION: "Model interpretation",
  HYPOTHESIS: "Unverified hypothesis",
};
const CITE_SPLIT = /(\[(?:S\d+|CALC-\d+)\])/g;

function useCiteOpener(art: Artifact) {
  const { open } = useEvidence();
  return (label: string) => {
    if (label.startsWith("CALC")) open({ tab: "calc", label, calc: art.calculations[label] ?? null });
    else open({ tab: "source", label, source: art.citations[label] ?? null, sourceId: art.citations[label]?.source_id });
  };
}

function CitedText({ text, art }: { text: string; art: Artifact }) {
  const openCite = useCiteOpener(art);
  return (
    <>
      {text.split(CITE_SPLIT).map((part, i) => {
        const m = part.match(/^\[(S\d+|CALC-\d+)\]$/);
        if (!m) return <Fragment key={i}>{part}</Fragment>;
        const label = m[1];
        return (
          <button key={i} type="button" className={clsx("ws-cite", label.startsWith("CALC") && "ws-cite-calc")}
                  onClick={() => openCite(label)} aria-label={`Open evidence ${label}`}>
            {label}
          </button>
        );
      })}
    </>
  );
}

function ClaimList({ claims, art, empty }: { claims: Claim[]; art: Artifact; empty: string }) {
  if (!claims.length) return <p className="ws-empty">{empty}</p>;
  return (
    <ul className="space-y-2.5">
      {claims.map((c) => (
        <li key={c.claim_id} className="text-[14px] leading-relaxed">
          <abbr title={EPI_TITLE[c.epistemic]} className={`ws-epi ws-epi-${c.epistemic} no-underline`}>{EPI_MARK[c.epistemic]}</abbr>
          <CitedText text={c.text} art={art} />
        </li>
      ))}
    </ul>
  );
}

function Section({ id, title, children, aside }: { id: string; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="ws-section" aria-labelledby={id}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={id} className="ws-h2">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function DataTable({ rows, art }: { rows: DataRow[]; art: Artifact }) {
  const { open } = useEvidence();
  const [view, setView] = useState<"table" | "chart">("table");
  const marginRows = rows.filter((r) => r.scale === "PERCENT");
  const inspect = (r: DataRow) => {
    if (r.ref?.startsWith("CALC")) open({ tab: "calc", label: r.ref, calc: art.calculations[r.ref], row: r });
    else open({ tab: "provenance", label: r.metric_label, row: r, sourceId: art.citations[r.source_label ?? ""]?.source_id });
  };
  return (
    <Section id="sec-data" title="Financial data & deterministic calculations"
             aside={<div className="ws-seg" role="group" aria-label="Data view">
               <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
               <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>Chart</button>
             </div>}>
      {view === "table" ? (
        <div className="ws-scroll-x">
          <table className="ws-table">
            <thead>
              <tr><th>Instrument</th><th>Metric</th><th>Period</th><th className="text-right">Value</th><th>Basis</th><th>As of</th><th>Ref</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.observation_id} className="is-clickable" onClick={() => inspect(r)}>
                  <td className="num">{r.symbol}</td>
                  <td>
                    <button type="button" className="text-left hover:text-brand" onClick={(e) => { e.stopPropagation(); inspect(r); }}>
                      {r.metric_label}
                    </button>
                  </td>
                  <td className="num text-muted">{r.fiscal_period}</td>
                  <td className="num text-right">{r.display}</td>
                  <td className="text-[12px] text-muted">{r.epistemic === "CALCULATED" ? "Calc" : r.basis.replace("_", " ")}</td>
                  <td className="num whitespace-nowrap">
                    {r.is_stale ? <span className="ws-tag ws-tag-warn" title={`${r.age_days} days old`}>Stale {r.as_of}</span> : <span className="text-muted">{r.as_of}</span>}
                  </td>
                  <td className="num text-[11px] text-subtle">{r.ref}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <PercentBars rows={marginRows} onSelect={inspect} />
      )}
      <p className="mt-2 text-[12px] text-subtle">
        Values are in each company&apos;s reporting currency and stated scale — never converted or mixed. Click any row for its audit trail.
      </p>
    </Section>
  );
}

function PercentBars({ rows, onSelect }: { rows: DataRow[]; onSelect: (r: DataRow) => void }) {
  if (!rows.length) return <p className="ws-empty">No percentage metrics to chart.</p>;
  const max = Math.max(...rows.map((r) => Math.abs(Number(r.value))), 1);
  return (
    <ul className="space-y-1.5" aria-label="Percentage metrics chart">
      {rows.map((r) => {
        const v = Number(r.value);
        return (
          <li key={r.observation_id}>
            <button type="button" onClick={() => onSelect(r)} className="grid w-full grid-cols-[150px_1fr_64px] items-center gap-3 text-left text-[12px]">
              <span className="truncate text-muted">{r.symbol} · {r.metric_label}</span>
              <span className="relative h-2.5 rounded-sm bg-sunken">
                <span className="absolute inset-y-0 left-0 rounded-sm" style={{ width: `${(Math.abs(v) / max) * 100}%`, background: v < 0 ? "hsl(var(--neg))" : "hsl(var(--brand))" }} />
              </span>
              <span className="num text-right">{r.display}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Exposures({ art }: { art: Artifact }) {
  const { open } = useEvidence();
  const [view, setView] = useState<"table" | "graph">("table");
  const select = (e: Exposure) => open({ tab: "edge", label: `${e.from} → ${e.to}`, edge: e, sourceId: art.citations[e.source_label ?? ""]?.source_id });
  const origins = art.resolution.resolved.map((i) => i.local_symbol ?? i.symbol);
  return (
    <Section id="sec-exposure" title="Second-order event & supply-chain exposure"
             aside={<div className="ws-seg" role="group" aria-label="Exposure view">
               <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
               <button type="button" aria-pressed={view === "graph"} onClick={() => setView("graph")}>Graph</button>
             </div>}>
      {!art.exposures.length ? (
        <p className="ws-empty">No documented supply-chain relationships for these instruments in AYVID&apos;s graph.</p>
      ) : view === "graph" ? (
        <ExposureGraph rows={art.exposures} origins={origins} onSelect={select} />
      ) : (
        <div className="ws-scroll-x">
          <table className="ws-table">
            <thead>
              <tr><th>Order</th><th>Path</th><th>Relationship</th><th>Transmission mechanism</th><th className="text-right">Dep.</th><th>Evidence</th><th>Ref</th></tr>
            </thead>
            <tbody>
              {art.exposures.map((e) => (
                <tr key={e.edge_id} className="is-clickable" onClick={() => select(e)}>
                  <td className="num">{e.direction === "UPSTREAM" ? "Up" : e.order}</td>
                  <td className="num whitespace-nowrap">
                    <button type="button" className="hover:text-brand" onClick={(ev) => { ev.stopPropagation(); select(e); }}>{e.path.join(" → ")}</button>
                  </td>
                  <td className="text-[12px]">{e.relationship_type}</td>
                  <td className="min-w-[220px] text-[12px] text-muted">{e.mechanism}</td>
                  <td className="num text-right">{e.revenue_dependency_pct != null ? `${e.revenue_dependency_pct}%` : "—"}</td>
                  <td><span className={clsx("ws-tag", e.evidence_status === "EXTRACTED" ? "ws-tag-pos" : "ws-tag-warn")}>{e.evidence_status}</span></td>
                  <td className="num text-[11px] text-subtle">{e.source_label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function ActionBar({ art }: { art: Artifact }) {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const theses = useTheses();
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const t = await researchApi.thesisFromResearch(art.session_id);
      await mutate("/theses");
      router.push(`/theses/${t.thesis_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save thesis");
      setBusy(false);
    }
  };
  const attach = async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await researchApi.attach(target, art.session_id);
      await mutate("/theses");
      router.push(`/theses/${target}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not attach");
      setBusy(false);
    }
  };
  return (
    <div className="sticky bottom-0 z-10 mt-8 flex flex-wrap items-center gap-3 border-t border-app bg-app py-3">
      <button type="button" className="ws-btn-primary" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save as thesis"}
      </button>
      <label className="sr-only" htmlFor="attach-target">Attach to existing thesis</label>
      <select id="attach-target" className="ws-input w-auto max-w-[240px]" value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="">Attach to existing thesis…</option>
        {(theses.data ?? []).map((t) => <option key={t.thesis_id} value={t.thesis_id}>{t.title}</option>)}
      </select>
      <button type="button" className="ws-btn" onClick={attach} disabled={busy || !target}>Attach</button>
      <span className="text-[12px] text-subtle">Saves verified citations, metrics and {art.monitor.length} suggested invalidation conditions.</span>
      {error ? <p role="alert" className="w-full text-[13px] text-neg">{error}</p> : null}
    </div>
  );
}

export function Banners({ art }: { art: Artifact }) {
  if (!art.banners.length) return null;
  return (
    <div className="mt-4 space-y-2" aria-live="polite">
      {art.banners.map((b, i) => (
        <p key={i} className={clsx("ws-banner", b.kind === "DEGRADED" || b.kind === "STALE" ? "ws-banner-warn" : "ws-banner-info")}>
          {b.kind === "DEGRADED" ? <strong className="mr-1">⚠</strong> : null}{b.message}
        </p>
      ))}
    </div>
  );
}

export function ResearchAnswer({ art }: { art: Artifact }) {
  const { setTelemetry, close } = useEvidence();
  useEffect(() => {
    setTelemetry(art.telemetry);
    return () => {
      setTelemetry(null);
      close();
    };
  }, [art, setTelemetry, close]);

  if (art.status === "REFUSED" && art.refusal) {
    return (
      <div className="ws-card mt-6 border-l-2" style={{ borderLeftColor: "hsl(var(--neg))" }} role="alert">
        <p className="label-mono text-neg">Research boundary · {art.refusal.code.replace(/_/g, " ")}</p>
        <p className="ws-prose mt-2">{art.refusal.message}</p>
        <p className="mt-3 text-[13px] text-muted">
          AYVID runs with <span className="num">PAPER_TRADING=true · AUTO_EXECUTE=false</span>; the research workspace has no path to order execution.
        </p>
      </div>
    );
  }
  if (art.status === "NEEDS_DISAMBIGUATION") {
    return (
      <div className="ws-card mt-6">
        <p className="label-mono text-warn">Ambiguous instrument</p>
        {art.resolution.ambiguous.map((a) => (
          <div key={a.token} className="mt-2">
            <p className="text-[14px]"><span className="num">{a.token}</span> — {a.reason}. Did you mean:</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {a.candidates.map((c) => (
                <li key={c.instrument_id}>
                  <Link className="ws-btn" href={`/research?q=${encodeURIComponent(`${c.symbol} research overview`)}&iid=${encodeURIComponent(c.instrument_id)}&run=1`}>
                    <span className="num">{c.instrument_id}</span> · {c.name}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {art.resolution.unsupported.length ? <p className="mt-3 text-[13px] text-muted">Unsupported: {art.resolution.unsupported.join(", ")}</p> : null}
      </div>
    );
  }
  if (!art.summary) return null;

  return (
    <article className="mt-6" aria-label="Research answer">
      <header>
        <div className="flex flex-wrap items-center gap-2">
          <span className="ws-tag">{art.intent.replace(/_/g, " ")}</span>
          <span className={clsx("ws-tag", art.data_mode === "LIVE" ? "ws-tag-pos" : "ws-tag-brand")}>Data: {art.data_mode}</span>
          {art.resolution.resolved.map((i) => (
            <Link key={i.instrument_id} href={`/companies/${encodeURIComponent(i.instrument_id)}`} className="ws-tag hover:text-fg">{i.instrument_id}</Link>
          ))}
        </div>
        <h1 className="ws-h1 mt-3">{art.title}</h1>
      </header>
      <Banners art={art} />

      <Section id="sec-synthesis" title="Executive synthesis">
        <p className="ws-prose mb-3 text-muted">{art.summary.headline}</p>
        {art.summary.llm_rewrite ? (
          <div className="mb-4">
            <p className="ws-prose"><CitedText text={art.summary.llm_rewrite} art={art} /></p>
            <p className="mt-1 text-[11px] text-subtle">Worded by {art.telemetry.provider} · {art.telemetry.model}; every citation and number audited against the facts below.</p>
          </div>
        ) : null}
        <ClaimList claims={art.summary.claims} art={art} empty="No verifiable claims." />
        <p className="mt-3 text-[11px] text-subtle">
          <span className="ws-epi">RPT</span>reported · <span className="ws-epi ws-epi-CALCULATED">CALC</span>calculated ·{" "}
          <span className="ws-epi">MGMT</span>management · <span className="ws-epi ws-epi-INTERPRETATION">INTERP</span>interpretation ·{" "}
          <span className="ws-epi ws-epi-HYPOTHESIS">HYP</span>hypothesis
        </p>
      </Section>

      <DataTable rows={art.data_table} art={art} />
      <Exposures art={art} />

      <Section id="sec-debate" title="Supporting evidence vs. counterarguments">
        <div className="ws-split">
          <div>
            <h3 className="mb-2 text-[13px] font-semibold text-pos">Supporting</h3>
            <ClaimList claims={art.bull} art={art} empty="No supporting evidence found in retrieved sources." />
          </div>
          <div>
            <h3 className="mb-2 text-[13px] font-semibold text-neg">Contradicting / bear case</h3>
            <ClaimList claims={art.bear} art={art} empty="No contradicting evidence found — absence of evidence is not evidence of absence." />
          </div>
        </div>
      </Section>

      <Section id="sec-uncertainty" title="Uncertainty, missing data & what to monitor">
        <ul className="space-y-1.5 text-[14px]">
          {art.uncertainty.map((u, i) => (
            <li key={i}><span className="ws-tag ws-tag-warn mr-2">{u.kind.replace(/_/g, " ")}</span>{u.text}</li>
          ))}
        </ul>
        <h3 className="mt-4 mb-2 text-[13px] font-semibold">Proposed invalidation conditions</h3>
        <ul className="space-y-1 text-[13px]">
          {art.monitor.map((m) => (
            <li key={`${m.instrument_id}-${m.metric_code}-${m.operator}`} className="num text-muted">• {m.description}</li>
          ))}
        </ul>
        <p className="mt-3 text-[12px] text-subtle">
          Audit: {art.audit.verified} citations verified, {art.audit.dropped} unverifiable claims dropped.
        </p>
      </Section>

      <ActionBar art={art} />
    </article>
  );
}
