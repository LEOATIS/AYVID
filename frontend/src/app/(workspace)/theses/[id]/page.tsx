"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import clsx from "clsx";
import { ApiAuthError, ApiNotFoundError } from "@/lib/api";
import {
  HEALTH_LABEL,
  STATUS_LABEL,
  researchApi,
  useCompany,
  type Condition,
  type EvidenceLink,
  type Evaluation,
  type Thesis,
} from "@/lib/research";
import { useEvidence } from "@/components/workspace/EvidenceDrawer";
import { HealthDot } from "@/components/workspace/WorkspaceShell";
import { ValuationSandbox } from "@/components/workspace/ValuationSandbox";

const STATUS_TONE: Record<Condition["current_status"], string> = {
  TRIGGERED: "ws-tag-neg",
  NOT_TRIGGERED: "ws-tag-pos",
  UNKNOWN_MISSING_DATA: "ws-tag-warn",
};
const METRIC_CHOICES = [
  "GROSS_MARGIN_PCT", "OPERATING_MARGIN_PCT", "NET_MARGIN_PCT", "REVENUE_GROWTH_PCT", "REVENUE",
  "REALIZED_VOL_ANN", "RETURN_90D_PCT", "MAX_DRAWDOWN_PCT", "CLOSE_PRICE", "CUSTOMER_CHURN_PCT", "CAPEX",
];

function useThesisMutation(id: string) {
  const { mutate } = useSWRConfig();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setBusy(true);
    setError(null);
    try {
      const out = await fn();
      await Promise.all([mutate(["thesis", id]), mutate("/theses")]);
      return out;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function ConditionBuilder({ thesis }: { thesis: Thesis }) {
  const { busy, error, run } = useThesisMutation(thesis.thesis_id);
  const [metric, setMetric] = useState("GROSS_MARGIN_PCT");
  const [op, setOp] = useState<"<" | ">" | "DROP_PCT" | "EVENT_OCCURS">("<");
  const [threshold, setThreshold] = useState("72");
  const [eventCode, setEventCode] = useState("TSM_SUPPLY_DISRUPTION");
  const [iid, setIid] = useState(thesis.instrument_ids[0]);
  const isEvent = op === "EVENT_OCCURS";

  const add = () => {
    const metric_code = isEvent ? `EVENT:${eventCode.toUpperCase()}` : metric;
    const sym = iid.split(":").at(-1);
    const description = isEvent
      ? `${eventCode} recorded`
      : `${sym} ${metric.replace(/_/g, " ").toLowerCase()} ${op === "DROP_PCT" ? "drops ≥" : op} ${threshold}${op === "DROP_PCT" ? "%" : ""}`;
    void run(() =>
      researchApi.addCondition(thesis.thesis_id, {
        metric_code, instrument_id: iid, operator: op, threshold_value: isEvent ? null : Number(threshold), description,
      }),
    );
  };
  return (
    <div className="ws-card mt-3">
      <p className="label-mono">Add invalidation condition</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1.2fr_1.2fr_0.8fr_0.8fr_auto] sm:items-end">
        <label className="text-[12px] text-muted">
          Instrument
          <select className="ws-input mt-1" value={iid} onChange={(e) => setIid(e.target.value)}>
            {thesis.instrument_ids.map((i) => <option key={i}>{i}</option>)}
          </select>
        </label>
        {isEvent ? (
          <label className="text-[12px] text-muted">
            Event code
            <input className="ws-input num mt-1" value={eventCode} autoComplete="off" spellCheck={false}
                   onChange={(e) => setEventCode(e.target.value.replace(/[^A-Za-z0-9_]/g, ""))} />
          </label>
        ) : (
          <label className="text-[12px] text-muted">
            Metric
            <select className="ws-input mt-1" value={metric} onChange={(e) => setMetric(e.target.value)}>
              {METRIC_CHOICES.map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
        )}
        <label className="text-[12px] text-muted">
          Operator
          <select className="ws-input mt-1" value={op} onChange={(e) => setOp(e.target.value as typeof op)}>
            <option value="<">&lt;</option>
            <option value=">">&gt;</option>
            <option value="DROP_PCT">drops %</option>
            <option value="EVENT_OCCURS">event occurs</option>
          </select>
        </label>
        <label className="text-[12px] text-muted">
          Threshold
          <input className="ws-input num mt-1" inputMode="decimal" disabled={isEvent} value={threshold} onChange={(e) => setThreshold(e.target.value)} />
        </label>
        <button type="button" className="ws-btn-primary justify-center" disabled={busy || (!isEvent && !Number.isFinite(Number(threshold)))} onClick={add}>
          Add
        </button>
      </div>
      {error ? <p role="alert" className="mt-2 text-[13px] text-neg">{error}</p> : null}
    </div>
  );
}

function Conditions({ thesis }: { thesis: Thesis }) {
  const { busy, run } = useThesisMutation(thesis.thesis_id);
  const [confirming, setConfirming] = useState<string | null>(null);
  return (
    <section className="ws-section" aria-labelledby="conds">
      <h2 id="conds" className="ws-h2">Invalidation &amp; monitoring conditions</h2>
      {!thesis.conditions.length ? (
        <p className="ws-empty">No conditions yet — a thesis without invalidation conditions cannot be monitored.</p>
      ) : (
        <ul className="space-y-2">
          {thesis.conditions.map((c) => (
            <li key={c.condition_id} className="ws-card !p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={clsx("ws-tag", STATUS_TONE[c.current_status])}>{STATUS_LABEL[c.current_status]}</span>
                <span className="text-[14px]">{c.description}</span>
                <span className="num ml-auto text-[11px] text-subtle">{c.metric_code} {c.operator} {c.threshold_value ?? ""}</span>
              </div>
              <p className="mt-1 text-[12px] text-muted">
                {c.evaluation_explanation}
                {c.observed_value != null ? <span className="num"> · observed {c.observed_value}</span> : null}
                {c.last_evaluated_at ? <span className="num text-subtle"> · {c.last_evaluated_at.replace("T", " ").slice(0, 16)}</span> : null}
              </p>
              <div className="mt-2 flex gap-2">
                {c.operator === "EVENT_OCCURS" ? (
                  <button type="button" className="ws-btn !py-1 text-[12px]" disabled={busy}
                          onClick={() => void run(() => researchApi.recordEvent(c.metric_code, `Recorded manually from thesis ${thesis.title}`))}>
                    Record this event
                  </button>
                ) : null}
                {confirming === c.condition_id ? (
                  <>
                    <button type="button" className="ws-btn !py-1 text-[12px] text-neg" disabled={busy}
                            onClick={() => void run(() => researchApi.removeCondition(thesis.thesis_id, c.condition_id)).then(() => setConfirming(null))}>
                      Confirm remove
                    </button>
                    <button type="button" className="ws-btn !py-1 text-[12px]" onClick={() => setConfirming(null)}>Cancel</button>
                  </>
                ) : (
                  <button type="button" className="ws-btn !py-1 text-[12px]" onClick={() => setConfirming(c.condition_id)}>Remove</button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <ConditionBuilder thesis={thesis} />
    </section>
  );
}

function EvidenceColumn({ title, rows, thesis, tone }: { title: string; rows: EvidenceLink[]; thesis: Thesis; tone: string }) {
  const { open } = useEvidence();
  const { busy, run } = useThesisMutation(thesis.thesis_id);
  const [confirming, setConfirming] = useState<string | null>(null);
  return (
    <div>
      <h3 className={clsx("mb-2 text-[13px] font-semibold", tone)}>{title} ({rows.length})</h3>
      {!rows.length ? (
        <p className="ws-empty">None linked.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((e) => (
            <li key={e.evidence_id} className="text-[13px]">
              <button type="button" className="text-left hover:text-brand" onClick={() => open({ tab: "source", label: e.doc_type ?? "Source", sourceId: e.source_id })}>
                <span className="ws-tag mr-1">{e.doc_type ?? "?"}</span>
                {e.source_title ?? e.source_id}
              </button>
              {e.note ? <p className="mt-0.5 text-[12px] text-muted">{e.note}</p> : null}
              {confirming === e.evidence_id ? (
                <span className="mt-1 flex gap-2">
                  <button type="button" className="ws-btn !py-0.5 text-[12px] text-neg" disabled={busy}
                          onClick={() => void run(() => researchApi.removeEvidence(thesis.thesis_id, e.evidence_id)).then(() => setConfirming(null))}>
                    Confirm unlink
                  </button>
                  <button type="button" className="ws-btn !py-0.5 text-[12px]" onClick={() => setConfirming(null)}>Cancel</button>
                </span>
              ) : (
                <button type="button" className="mt-1 py-1 text-[12px] text-subtle hover:text-neg" disabled={busy}
                        aria-label={`Unlink ${e.source_title ?? e.source_id}`} onClick={() => setConfirming(e.evidence_id)}>
                  Unlink
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AddEvidence({ thesis }: { thesis: Thesis }) {
  const company = useCompany(thesis.instrument_ids[0]);
  const { busy, error, run } = useThesisMutation(thesis.thesis_id);
  const [sourceId, setSourceId] = useState("");
  const [stance, setStance] = useState<"SUPPORTING" | "CONTRADICTING">("CONTRADICTING");
  const [note, setNote] = useState("");
  return (
    <div className="ws-card mt-3 grid gap-2 sm:grid-cols-[1.6fr_0.8fr_1.2fr_auto] sm:items-end">
      <label className="text-[12px] text-muted">
        Source
        <select className="ws-input mt-1" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
          <option value="">Choose a source…</option>
          {(company.data?.sources ?? []).map((s) => <option key={s.source_id} value={s.source_id}>{s.doc_type} · {s.title}</option>)}
        </select>
      </label>
      <label className="text-[12px] text-muted">
        Stance
        <select className="ws-input mt-1" value={stance} onChange={(e) => setStance(e.target.value as typeof stance)}>
          <option value="SUPPORTING">Supporting</option>
          <option value="CONTRADICTING">Contradicting</option>
        </select>
      </label>
      <label className="text-[12px] text-muted">
        Note
        <input className="ws-input mt-1" value={note} autoComplete="off" maxLength={500} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button type="button" className="ws-btn-primary justify-center" disabled={busy || !sourceId}
              onClick={() => void run(() => researchApi.addEvidence(thesis.thesis_id, { source_id: sourceId, stance, note })).then(() => setNote(""))}>
        Link
      </button>
      {error ? <p role="alert" className="text-[13px] text-neg sm:col-span-4">{error}</p> : null}
    </div>
  );
}

function WhatChanged({ thesis }: { thesis: Thesis }) {
  const { busy, error, run } = useThesisMutation(thesis.thesis_id);
  const [ev, setEv] = useState<Evaluation | null>(null);
  const [approved, setApproved] = useState(false);
  const evaluate = () =>
    void run(() => researchApi.evaluate(thesis.thesis_id)).then((r) => {
      if (r) {
        setEv(r);
        setApproved(false);
      }
    });
  return (
    <section className="ws-section" aria-labelledby="wc">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="wc" className="ws-h2">What changed?</h2>
        <button type="button" className="ws-btn-primary" disabled={busy} onClick={evaluate}>{busy ? "Evaluating…" : "Run check now"}</button>
      </div>
      <p className="text-[12px] text-subtle">
        Last reviewed {thesis.last_reviewed_at?.replace("T", " ").slice(0, 16) ?? "never"}. Conditions with missing data are UNKNOWN — never “not triggered”.
      </p>
      {error ? <p role="alert" className="mt-2 text-[13px] text-neg">{error}</p> : null}
      {ev ? (
        <div className="ws-card mt-3" aria-live="polite">
          <p className="flex items-center gap-2 text-[14px]">
            <HealthDot health={ev.health} /> {HEALTH_LABEL[ev.health]}
            <span className="num ml-auto text-[12px] text-muted">
              {ev.counts.TRIGGERED} triggered · {ev.counts.NOT_TRIGGERED} not triggered · {ev.counts.UNKNOWN_MISSING_DATA} unknown
            </span>
          </p>
          <ul className="mt-2 space-y-1 text-[13px]">
            {ev.results.map((r) => (
              <li key={r.condition_id}>
                <span className={clsx("ws-tag mr-2", STATUS_TONE[r.status])}>{STATUS_LABEL[r.status]}</span>
                {r.description}
                {r.status !== r.previous_status ? <span className="ws-tag ws-tag-brand ml-2">changed from {STATUS_LABEL[r.previous_status]}</span> : null}
                {r.stale ? <span className="ws-tag ws-tag-warn ml-2">stale input</span> : null}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[12px] text-muted">
            New evidence since last review: {ev.new_evidence.length ? ev.new_evidence.map((n) => n.title).join("; ") : "none found in retrieved sources"}.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-app pt-3">
            <p className="text-[13px]">Proposed revision: <span className="text-muted">{ev.proposed_revision_note}</span></p>
            <button type="button" className="ws-btn ml-auto" disabled={busy || approved}
                    onClick={() => void run(() => researchApi.approveRevision(thesis.thesis_id, `Approved evaluation: ${ev.proposed_revision_note}`)).then((r) => r && setApproved(true))}>
              {approved ? "Revision recorded" : "Approve as new revision"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function SummaryEditor({ thesis }: { thesis: Thesis }) {
  const { busy, error, run } = useThesisMutation(thesis.thesis_id);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(thesis.title);
  const [summary, setSummary] = useState(thesis.summary);
  if (!editing) {
    return (
      <div>
        <h1 className="ws-h1 mt-2">{thesis.title}</h1>
        <p className="ws-prose mt-3 text-muted">{thesis.summary || "No summary yet."}</p>
        <button type="button" className="ws-btn mt-3 !py-1 text-[12px]" onClick={() => setEditing(true)}>Edit statement</button>
      </div>
    );
  }
  return (
    <form
      className="mt-2 space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        void run(() => researchApi.updateThesis(thesis.thesis_id, { title, summary, change_note: "Edited thesis statement" })).then((r) => r && setEditing(false));
      }}
    >
      <label className="block text-[12px] text-muted">
        Title
        <input className="ws-input mt-1" value={title} minLength={3} maxLength={200} required onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="block text-[12px] text-muted">
        Summary
        <textarea className="ws-input mt-1" rows={4} maxLength={4000} value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <div className="flex gap-2">
        <button type="submit" className="ws-btn-primary" disabled={busy}>{busy ? "Saving…" : "Save revision"}</button>
        <button type="button" className="ws-btn" onClick={() => setEditing(false)}>Cancel</button>
      </div>
      {error ? <p role="alert" className="text-[13px] text-neg">{error}</p> : null}
    </form>
  );
}

function ThesisValuation({ thesis }: { thesis: Thesis }) {
  const company = useCompany(thesis.instrument_ids[0]);
  const obs = company.data?.observations ?? [];
  const rev = obs.filter((o) => o.metric_code === "REVENUE" && o.fiscal_period.startsWith("FY")).at(-1);
  const opm = obs.filter((o) => o.metric_code === "OPERATING_MARGIN_PCT").at(-1);
  return (
    <section className="ws-section" aria-labelledby="val">
      <h2 id="val" className="ws-h2">Valuation &amp; scenario sandbox · {thesis.instrument_ids[0]}</h2>
      <ValuationSandbox baseRevenue={rev ? Number(rev.value) : null} baseLabel={rev?.fiscal_period} currency={rev?.currency}
                        observedMargin={opm ? Number(opm.value) : null} />
    </section>
  );
}

export default function ThesisPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, isLoading } = useSWR<Thesis>(["thesis", id], () => researchApi.thesis(id), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });

  if (isLoading) return <p className="ws-empty">Loading thesis…</p>;
  if (error) {
    const forbidden = error instanceof ApiAuthError;
    const notFound = error instanceof ApiNotFoundError;
    return (
      <div role="alert" className="ws-card border-l-2" style={{ borderLeftColor: "hsl(var(--neg))" }}>
        <p className="label-mono text-neg">{forbidden ? "403 Forbidden · permission boundary" : notFound ? "404 · not found" : "Error"}</p>
        <p className="ws-prose mt-2">
          {forbidden
            ? "This thesis belongs to another workspace. AYVID blocks reads and edits at the API layer, so nothing from it was loaded."
            : notFound
              ? "This thesis does not exist."
              : "Could not load the thesis. Is the API running?"}
        </p>
        {forbidden && error.requestId ? <p className="num mt-1 text-[11px] text-subtle">Request ID {error.requestId}</p> : null}
        <Link className="ws-link mt-3 inline-block" href="/theses">Back to your theses</Link>
      </div>
    );
  }
  if (!data) return null;
  const health = data.health ?? "UNKNOWN";

  return (
    <article>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-2 text-[13px]"><HealthDot health={health} /> {HEALTH_LABEL[health]}</span>
        <span className="ws-tag">{data.conviction_label.replace(/_/g, " ")}</span>
        <span className="ws-tag">Horizon {data.horizon}</span>
        {data.instrument_ids.map((i) => (
          <Link key={i} className="ws-tag hover:text-fg" href={`/companies/${encodeURIComponent(i)}`}>{i}</Link>
        ))}
      </div>
      <SummaryEditor key={data.updated_at} thesis={data} />

      <WhatChanged thesis={data} />
      <Conditions thesis={data} />

      <section className="ws-section" aria-labelledby="ev">
        <h2 id="ev" className="ws-h2">Evidence ledger</h2>
        <div className="ws-split">
          <EvidenceColumn title="Supporting" rows={data.supporting_evidence} thesis={data} tone="text-pos" />
          <EvidenceColumn title="Contradicting" rows={data.contradicting_evidence} thesis={data} tone="text-neg" />
        </div>
        <AddEvidence thesis={data} />
      </section>

      {data.assumptions.length || data.open_questions.length ? (
        <section className="ws-section ws-split">
          <div>
            <h2 className="ws-h2">Assumptions</h2>
            <ul className="space-y-1 text-[13px] text-muted">{data.assumptions.map((a, i) => <li key={i}>• {a}</li>)}</ul>
          </div>
          <div>
            <h2 className="ws-h2">Open questions</h2>
            <ul className="space-y-1 text-[13px] text-muted">{data.open_questions.map((a, i) => <li key={i}>• {a}</li>)}</ul>
          </div>
        </section>
      ) : null}

      <ThesisValuation thesis={data} />

      <section className="ws-section" aria-labelledby="rev">
        <h2 id="rev" className="ws-h2">Revision history (append-only)</h2>
        <ol className="space-y-1 text-[13px]">
          {data.revisions.map((r) => (
            <li key={r.revision_id} className="grid grid-cols-[48px_140px_1fr] gap-2">
              <span className="num text-subtle">r{r.revision_no}</span>
              <span className="num text-subtle">{r.created_at.replace("T", " ").slice(0, 16)}</span>
              <span>{r.change_note}</span>
            </li>
          ))}
        </ol>
        {data.linked_session_ids.length ? (
          <p className="mt-3 text-[12px] text-muted">
            Linked research:{" "}
            {data.linked_session_ids.map((s) => <Link key={s} className="ws-link num mr-2" href={`/research/${s}`}>{s}</Link>)}
          </p>
        ) : null}
      </section>
    </article>
  );
}
