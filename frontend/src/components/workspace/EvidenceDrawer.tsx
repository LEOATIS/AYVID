"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import clsx from "clsx";
import {
  researchApi,
  type Calculation,
  type CalcInput,
  type DataRow,
  type EdgeRow,
  type Exposure,
  type SourceDoc,
  type Telemetry,
} from "@/lib/research";

export type DrawerTab = "source" | "calc" | "provenance" | "edge";

interface DrawerState {
  tab: DrawerTab;
  label?: string;
  source?: SourceDoc | null;
  sourceId?: string | null;
  calc?: Calculation | null;
  row?: DataRow | null;
  edge?: Exposure | EdgeRow | null;
}

interface EvidenceCtx {
  state: DrawerState | null;
  open: (s: DrawerState) => void;
  close: () => void;
  telemetry: Telemetry | null;
  setTelemetry: (t: Telemetry | null) => void;
}

const Ctx = createContext<EvidenceCtx | null>(null);

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerState | null>(null);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const open = useCallback((s: DrawerState) => setState(s), []);
  const close = useCallback(() => setState(null), []);
  const value = useMemo(() => ({ state, open, close, telemetry, setTelemetry }), [state, open, close, telemetry]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useEvidence(): EvidenceCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useEvidence must be used inside EvidenceProvider");
  return ctx;
}

const TABS: { key: DrawerTab; label: string }[] = [
  { key: "source", label: "Source" },
  { key: "calc", label: "Calculation" },
  { key: "provenance", label: "Provenance" },
  { key: "edge", label: "Causal edge" },
];

function Field({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[112px_1fr] gap-3 py-1.5 text-[13px]">
      <dt className="text-subtle">{k}</dt>
      <dd className={clsx("min-w-0 break-words text-fg", mono && "num text-[12px]")}>{v ?? "—"}</dd>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="ws-empty">{text}</p>;
}

function accessLabel(doc: SourceDoc): { text: string; tone: string } {
  if (doc.access_status === "RESTRICTED") return { text: "Source access restricted", tone: "warn" };
  if (doc.access_status === "UNAVAILABLE") return { text: "Source unavailable", tone: "neg" };
  if (!doc.excerpt) return { text: "No supporting evidence found", tone: "warn" };
  return { text: "Available", tone: "pos" };
}

function SourceTab({ state }: { state: DrawerState }) {
  const [doc, setDoc] = useState<SourceDoc | null>(state.source ?? null);
  const [error, setError] = useState<string | null>(null);
  const id = state.source?.source_id ?? state.sourceId ?? null;

  useEffect(() => {
    setError(null);
    if (state.source) {
      setDoc(state.source);
      return;
    }
    if (!id) {
      setDoc(null);
      return;
    }
    let live = true;
    researchApi
      .source(id)
      .then((d) => live && setDoc(d))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Source unavailable"));
    return () => {
      live = false;
    };
  }, [id, state.source]);

  if (error) return <Empty text={`Source unavailable — ${error}`} />;
  if (!id) return <Empty text="Select a citation [S#] to inspect its source." />;
  if (!doc) return <p className="ws-empty">Loading…</p>;
  const access = accessLabel(doc);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className={`ws-tag ws-tag-${access.tone}`}>{access.text}</span>
        {doc.is_fixture ? <span className="ws-tag ws-tag-warn">Curated filing summary</span> : null}
        <span className="ws-tag">{doc.provider.replace(/_/g, " ")}</span>
      </div>
      <h3 className="text-[15px] font-medium leading-snug text-fg" style={{ textWrap: "balance" }}>{doc.title}</h3>
      {doc.excerpt ? (
        <blockquote className="ws-excerpt">
          <mark>{doc.excerpt}</mark>
        </blockquote>
      ) : null}
      {doc.is_fixture ? (
        <p className="mt-2 text-[12px] text-subtle">
          Summary transcribed from the public filing — not a verbatim quote. Verify against the primary document.
        </p>
      ) : null}
      <dl className="mt-3 border-t border-app pt-2">
        <Field k="Document" v={doc.doc_type} />
        <Field k="Locator" v={doc.locator} />
        <Field k="Accession no." v={doc.accession_no ?? "Not recorded (verify on EDGAR)"} mono />
        <Field k="Published" v={doc.published_at} mono />
        <Field k="Retrieved" v={doc.retrieved_at} mono />
        <Field k="Content hash" v={doc.content_hash} mono />
        <Field k="Instrument" v={doc.instrument_id} mono />
        <Field
          k="Canonical URL"
          v={
            doc.canonical_url?.startsWith("http") ? (
              <a className="ws-link" href={doc.canonical_url} target="_blank" rel="noreferrer noopener">
                Open on SEC EDGAR ↗
              </a>
            ) : (
              doc.canonical_url
            )
          }
        />
      </dl>
    </div>
  );
}

function CalcTab({ calc }: { calc?: Calculation | null }) {
  if (!calc) return <Empty text="Select a derived metric or [CALC-#] to audit its arithmetic." />;
  const inputs: CalcInput[] = Array.isArray(calc.inputs) ? calc.inputs : [];
  const meta = Array.isArray(calc.inputs) ? null : (calc.inputs as Record<string, unknown>);
  return (
    <div>
      <p className="label-mono mb-1">{calc.label}</p>
      <h3 className="text-[15px] font-medium text-fg">{calc.label_text}</h3>
      <div className="ws-formula num mt-3">{calc.formula}</div>
      <h4 className="label-mono mt-4 mb-1">Named inputs</h4>
      {inputs.length ? (
        <table className="ws-table">
          <thead>
            <tr><th>Input</th><th>Period</th><th className="text-right">Value</th><th>Source</th></tr>
          </thead>
          <tbody>
            {inputs.map((i) => (
              <tr key={`${i.name}-${i.fiscal_period}`}>
                <td>{i.name}</td>
                <td className="num">{i.fiscal_period}</td>
                <td className="num text-right">{i.value}{i.currency ? ` ${i.currency}` : ""} {i.scale === "MILLIONS" ? "m" : ""}</td>
                <td className="num text-[11px]">{i.source_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : meta ? (
        <dl>{Object.entries(meta).map(([k, v]) => <Field key={k} k={k.replace(/_/g, " ")} v={String(v)} mono />)}</dl>
      ) : (
        <Empty text="No inputs recorded." />
      )}
      <h4 className="label-mono mt-4 mb-1">Assumptions</h4>
      <p className="text-[13px] text-muted">
        Ratio inputs must share fiscal period, currency and scale; growth inputs must share metric, currency and scale —
        enforced in <code className="num">research_calc.assert_comparable</code>. Daily-bar statistics use 252 trading days per year.
      </p>
      <div className="mt-4 flex items-baseline justify-between border-t border-app pt-3">
        <span className="label-mono">Deterministic output</span>
        <span className="num text-[20px] text-fg">{calc.result}{calc.unit === "%" ? "%" : ""}</span>
      </div>
      <p className="mt-1 text-right text-[11px] text-subtle num">as of {calc.as_of}</p>
    </div>
  );
}

function ProvenanceTab({ row }: { row?: DataRow | null }) {
  if (!row) return <Empty text="Select a metric cell to see its provenance." />;
  return (
    <div>
      {row.is_stale ? (
        <p className="ws-banner ws-banner-warn mb-3">STALE DATA (As of {row.as_of}) — {row.age_days} days old.</p>
      ) : null}
      <dl>
        <Field k="Instrument" v={row.instrument_id} mono />
        <Field k="Metric" v={`${row.metric_label} (${row.metric_code})`} />
        <Field k="Value" v={row.display} mono />
        <Field k="Fiscal period" v={row.fiscal_period} mono />
        <Field k="Currency" v={row.currency ?? "n/a"} mono />
        <Field k="Scale" v={row.scale} mono />
        <Field k="Basis" v={row.basis.replace(/_/g, " ")} />
        <Field k="Status" v={row.status} />
        <Field k="Restatement" v="None recorded" />
        <Field k="As of" v={row.as_of} mono />
        <Field k="Observation" v={row.observation_id} mono />
      </dl>
    </div>
  );
}

function EdgeTab({ edge }: { edge?: Exposure | EdgeRow | null }) {
  if (!edge) return <Empty text="Select a supply-chain edge to inspect it." />;
  const from = "from" in edge ? edge.from : edge.src_symbol;
  const to = "to" in edge ? edge.to : edge.dst_symbol;
  return (
    <div>
      <p className="num text-[18px] text-fg">{from} → {to}</p>
      {"mechanism" in edge ? <p className="mt-1 text-[13px] text-muted">{edge.mechanism}</p> : null}
      <dl className="mt-3">
        <Field k="Relationship" v={edge.relationship_type} />
        <Field k="Evidence" v={<span className={`ws-tag ${edge.evidence_status === "EXTRACTED" ? "ws-tag-pos" : "ws-tag-warn"}`}>{edge.evidence_status}</span>} />
        <Field k="Revenue dep." v={edge.revenue_dependency_pct != null ? `${edge.revenue_dependency_pct}% (estimate)` : "Not disclosed"} mono />
        <Field k="Criticality" v={edge.criticality_score} mono />
        {"order" in edge ? <Field k="Order" v={`${edge.order} (${edge.direction.toLowerCase()})`} /> : null}
        {"path" in edge ? <Field k="Path" v={edge.path.join(" → ")} mono /> : null}
        <Field k="Uncertainty" v={edge.uncertainty_note} />
        <Field k="Edge id" v={edge.edge_id} mono />
      </dl>
    </div>
  );
}

function TelemetryPanel({ t }: { t: Telemetry }) {
  return (
    <details className="mt-6 border-t border-app pt-3">
      <summary className="label-mono cursor-pointer">Model &amp; retrieval telemetry</summary>
      <dl className="mt-2">
        <Field k="Engine" v={t.llm_used ? `${t.provider} · ${t.model}` : `Deterministic synthesis${t.provider !== "deterministic" ? ` (${t.provider} available)` : ""}`} />
        <Field k="LLM latency" v={t.latency_ms != null ? `${t.latency_ms} ms` : "—"} mono />
        <Field k="Tokens in/out" v={t.input_tokens != null ? `${t.input_tokens} / ${t.output_tokens}` : "—"} mono />
        <Field k="Sources" v={t.retrieval?.source_documents} mono />
        <Field k="Live EDGAR" v={t.retrieval?.live_edgar_documents} mono />
        <Field k="Run time" v={t.total_ms != null ? `${t.total_ms} ms` : "—"} mono />
        {t.note ? <Field k="Note" v={t.note} /> : null}
      </dl>
    </details>
  );
}

export function EvidenceDrawer() {
  const { state, open, close, telemetry } = useEvidence();
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [state, close]);

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      aria-label="Evidence inspector"
      className={clsx("ws-drawer", state && "is-open")}
      aria-hidden={!state}
    >
      <div className="flex items-center justify-between border-b border-app px-4 py-3">
        <p className="label-mono">Evidence{state?.label ? ` · ${state.label}` : ""}</p>
        <button type="button" className="ws-icon-btn" aria-label="Close evidence inspector" onClick={close}>
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div role="tablist" aria-label="Inspector views" className="ws-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={state?.tab === t.key}
            className="ws-tab"
            onClick={() => state && open({ ...state, tab: t.key })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="ws-drawer-body" aria-live="polite">
        {state?.tab === "source" && <SourceTab state={state} />}
        {state?.tab === "calc" && <CalcTab calc={state.calc} />}
        {state?.tab === "provenance" && <ProvenanceTab row={state.row} />}
        {state?.tab === "edge" && <EdgeTab edge={state.edge} />}
        {telemetry ? <TelemetryPanel t={telemetry} /> : null}
      </div>
    </aside>
  );
}
