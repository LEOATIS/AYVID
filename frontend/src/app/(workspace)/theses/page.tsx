"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { HEALTH_LABEL, researchApi, useCompanies, useTheses } from "@/lib/research";
import { HealthDot } from "@/components/workspace/WorkspaceShell";

const MISSING_DATA_DEMO = {
  title: "Demo — NVDA customer churn (missing-data case)",
  instrument_ids: ["US:NASDAQ:NVDA"],
  summary: "Edge-case demo: churn is not disclosed in NVIDIA's 10-K, so this condition must evaluate to UNKNOWN, never 0 or 'not triggered'.",
  conviction_label: "EXPLORATORY",
  conditions: [
    { metric_code: "CUSTOMER_CHURN_PCT", instrument_id: "US:NASDAQ:NVDA", operator: ">", threshold_value: 5, description: "NVDA segment customer churn > 5%" },
    { metric_code: "GROSS_MARGIN_PCT", instrument_id: "US:NASDAQ:NVDA", operator: "<", threshold_value: 72, description: "NVDA quarterly gross margin < 72.0%" },
  ],
};

function NewThesisForm() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const companies = useCompanies();
  const [title, setTitle] = useState("");
  const [iid, setIid] = useState("US:NASDAQ:NVDA");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const t = await researchApi.createThesis({ title, instrument_ids: [iid] });
      await mutate("/theses");
      router.push(`/theses/${t.thesis_id}`);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Could not create thesis");
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="ws-card mt-5 grid gap-3 sm:grid-cols-[1fr_220px_auto] sm:items-end" aria-label="New thesis">
      <label className="text-[12px] text-muted">
        Thesis statement
        <input className="ws-input mt-1" name="title" required minLength={3} maxLength={200} autoComplete="off"
               placeholder="e.g. NVDA data-centre demand outlasts China export limits…" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="text-[12px] text-muted">
        Instrument
        <select className="ws-input mt-1" name="instrument" value={iid} onChange={(e) => setIid(e.target.value)}>
          {(companies.data ?? [{ instrument_id: iid }]).map((c) => <option key={c.instrument_id} value={c.instrument_id}>{c.instrument_id}</option>)}
        </select>
      </label>
      <button type="submit" className="ws-btn-primary justify-center" disabled={busy || title.trim().length < 3}>
        {busy ? "Creating…" : "Create from scratch"}
      </button>
      {err ? <p role="alert" className="text-[13px] text-neg sm:col-span-3">{err}</p> : null}
    </form>
  );
}

function ThesesBody() {
  const { data, error, isLoading } = useTheses();
  const params = useSearchParams();
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const demoRan = useRef(false);
  const [demoErr, setDemoErr] = useState<string | null>(null);

  useEffect(() => {
    if (params.get("demo") !== "missing-data" || demoRan.current) return;
    demoRan.current = true;
    // Drop the param first so a reload can't create a second demo thesis.
    window.history.replaceState(null, "", "/theses");
    researchApi
      .createThesis(MISSING_DATA_DEMO)
      .then(async (t) => {
        await mutate("/theses");
        router.replace(`/theses/${t.thesis_id}`);
      })
      .catch((e: unknown) => setDemoErr(e instanceof Error ? e.message : "Demo failed"));
  }, [params, router, mutate]);

  return (
    <div>
      <p className="label-mono">Theses</p>
      <h1 className="ws-h1 mt-2">Persistent theses with explicit invalidation conditions</h1>
      <p className="mt-2 text-[14px] text-muted">
        Save one from research, or start from scratch. Each condition is evaluated deterministically by “What changed?”.
      </p>
      {params.get("demo") ? <p className="ws-banner ws-banner-info mt-4" aria-live="polite">{demoErr ?? "Creating missing-data demo thesis…"}</p> : null}
      <NewThesisForm />
      {isLoading ? <p className="ws-empty mt-4">Loading…</p> : null}
      {error ? <p role="alert" className="ws-banner ws-banner-neg mt-4">Could not load theses.</p> : null}
      {data && !data.length ? (
        <p className="ws-empty mt-4">No theses yet. <Link className="ws-link" href="/research">Run research</Link> and click “Save as thesis”.</p>
      ) : null}
      {data?.length ? (
        <div className="ws-scroll-x mt-5">
          <table className="ws-table">
            <thead><tr><th>Health</th><th>Thesis</th><th>Instruments</th><th>Conditions</th><th>Evidence label</th><th>Updated</th></tr></thead>
            <tbody>
              {data.map((t) => (
                <tr key={t.thesis_id}>
                  <td><span className="inline-flex items-center gap-2"><HealthDot health={t.health} /> {HEALTH_LABEL[t.health]}</span></td>
                  <td><Link className="ws-link" href={`/theses/${t.thesis_id}`}>{t.title}</Link></td>
                  <td className="num text-[12px]">{t.instrument_ids.join(", ")}</td>
                  <td className="num text-[12px]">
                    {t.condition_statuses.filter((s) => s === "TRIGGERED").length} trig ·{" "}
                    {t.condition_statuses.filter((s) => s === "UNKNOWN_MISSING_DATA").length} unknown · {t.condition_statuses.length} total
                  </td>
                  <td className="text-[12px] text-muted">{t.conviction_label.replace(/_/g, " ").toLowerCase()}</td>
                  <td className="num text-[12px] text-muted">{t.updated_at.slice(0, 16).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export default function ThesesPage() {
  return (
    <Suspense fallback={<p className="ws-empty">Loading…</p>}>
      <ThesesBody />
    </Suspense>
  );
}
