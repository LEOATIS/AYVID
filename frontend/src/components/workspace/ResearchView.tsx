"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { useSWRConfig } from "swr";
import { ApiError } from "@/lib/api";
import { researchApi, type Artifact } from "@/lib/research";
import { ResearchAnswer } from "./ResearchAnswer";
import { FOCUS_QUERY_EVENT } from "./WorkspaceShell";

export const CORE_QUERY =
  "What is NVDA's supply-chain exposure to TSMC and how do its recent margins, volatility regime, and China restrictions affect a long-term thesis?";

export const OUTAGE_QUERY =
  "How does the Microsoft Azure and M365 cascading outage propagate across cloud, observability, and cybersecurity stocks (MSFT, GOOGL, AMZN, DDOG, CRWD, NET)?";

export const SAMPLE_QUERIES = [
  CORE_QUERY,
  OUTAGE_QUERY,
  "How does a Taiwan fab disruption propagate to NVDA, AAPL, and ASML?",
  "Analyze NVDA gross margin trajectory, customer concentration, and key invalidation risks",
  "Compare ASML vs. AMAT capital intensity and China revenue exposure",
];

const PIPELINE = [
  "Resolving instrument & period",
  "Retrieving SEC filings & live prices",
  "Computing deterministic metrics & causal paths",
  "Synthesizing cited analysis & auditing claims",
];

function researchHref(q: string, extra = ""): string {
  return `/research?q=${encodeURIComponent(q)}&run=1${extra}`;
}

export const SCENARIOS: { label: string; hint: string; href: string }[] = [
  { label: "Core loop", hint: "NVDA × TSMC, cited answer → save thesis", href: researchHref(CORE_QUERY) },
  { label: "Cloud outage", hint: "MSFT outage → GOOGL, AMZN, DDOG, CRWD, NET", href: researchHref(OUTAGE_QUERY) },
  { label: "Missing data", hint: "Churn condition → UNKNOWN, never 0", href: "/theses?demo=missing-data" },
  { label: "Stale data", hint: "Shows how stale data is labelled", href: researchHref("INFY.NS volatility regime and 90-day momentum") },
  { label: "Provider failure", hint: "SEC EDGAR outage → degraded mode", href: researchHref(CORE_QUERY, "&simulate=edgar") },
  { label: "Unsafe request", hint: "Trade execution → boundary refusal", href: researchHref("Execute a live market buy of 500 NVDA options right now") },
  { label: "Permission boundary", hint: "Another workspace's thesis → 403", href: "/theses/th_private_partner" },
  { label: "Ambiguous ticker", hint: "'ON' as word vs ticker", href: researchHref("Is ON a good name to research?") },
];

export function ScenarioSwitcher() {
  return (
    <details className="ws-card mt-6">
      <summary className="cursor-pointer text-[13px] font-medium">Verification &amp; edge-case scenarios</summary>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {SCENARIOS.map((s) => (
          <li key={s.label}>
            <Link href={s.href} className="ws-nav flex-col items-start gap-0 border border-app">
              <span className="text-[13px] text-fg">{s.label}</span>
              <span className="text-[12px] text-subtle">{s.hint}</span>
            </Link>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Stepper({ running, art }: { running: boolean; art: Artifact | null }) {
  if (!running && !art) return null;
  const steps = art?.steps ?? [];
  return (
    <section aria-label="Research pipeline" className="mt-5">
      <ol className="ws-steps" aria-live="polite">
        {PIPELINE.map((label, i) => {
          const s = steps.find((x) => x.label === label);
          const status = s?.status ?? (running ? "running" : "skipped");
          return (
            <li key={label} className={clsx("ws-step", s && `is-${s.status}`, running && "is-active")}>
              <span className="ws-step-mark" aria-hidden>
                {status === "done" ? "✓" : status === "degraded" ? "!" : status === "blocked" ? "×" : status === "running" ? "…" : "–"}
              </span>
              <span>
                {i + 1}. {label}
                {s ? <span className="block text-[12px] text-subtle">{s.detail}</span> : null}
              </span>
              <span className="num text-[11px] text-subtle">{s ? `${s.ms} ms` : running ? "running" : "skipped"}</span>
            </li>
          );
        })}
      </ol>
      {running ? <p className="mt-2 text-[12px] text-subtle">Running the pipeline… stage timings are reported when the run completes.</p> : null}
    </section>
  );
}

export function ResearchView({ initial }: { initial?: Artifact | null }) {
  const router = useRouter();
  const params = useSearchParams();
  const { mutate } = useSWRConfig();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = useState(initial?.query ?? params.get("q") ?? "");
  const [art, setArt] = useState<Artifact | null>(initial ?? null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ message: string; requestId: string | null } | null>(null);
  const autoRanFor = useRef<string | null>(null);
  const inFlight = useRef(false);

  const run = useCallback(
    async (q: string, simulate?: string | null, instrumentIds?: string[]) => {
      // Ref, not state: a held Enter key fires before React re-renders `running`.
      if (q.trim().length < 3 || inFlight.current) return;
      inFlight.current = true;
      setRunning(true);
      setError(null);
      setArt(null);
      try {
        const result = await researchApi.query(q.trim(), { simulate, instrumentIds });
        setArt(result);
        // Prime the session page's cache so navigating there doesn't flash a reload.
        await mutate(["session", result.session_id], result, { revalidate: false });
        void mutate("/research/sessions?limit=12");
        router.replace(`/research/${result.session_id}`, { scroll: false });
      } catch (e) {
        setError({
          message: e instanceof Error ? e.message : "Research request failed",
          requestId: e instanceof ApiError ? e.requestId : null,
        });
      } finally {
        inFlight.current = false;
        setRunning(false);
      }
    },
    [mutate, router],
  );

  useEffect(() => {
    const key = params.toString();
    if (initial || params.get("run") !== "1" || autoRanFor.current === key) return;
    const q = params.get("q");
    if (!q) return;
    autoRanFor.current = key;
    setQuery(q);
    const iid = params.get("iid");
    void run(q, params.get("simulate"), iid ? [iid] : undefined);
  }, [params, initial, run]);

  useEffect(() => {
    const focus = () => inputRef.current?.focus();
    window.addEventListener(FOCUS_QUERY_EVENT, focus);
    return () => window.removeEventListener(FOCUS_QUERY_EVENT, focus);
  }, []);

  return (
    <div>
      {!art && !running ? (
        <header className="mb-5">
          <p className="label-mono">Research workspace</p>
          <h1 className="ws-h1 mt-2">Ask a question. Every claim comes back with its source.</h1>
          <p className="mt-2 max-w-[62ch] text-[14px] text-muted">
            AYVID resolves the instrument, retrieves filings and local market data, computes every number deterministically,
            and drops any claim it cannot cite.
          </p>
        </header>
      ) : null}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(query);
        }}
        aria-label="Research query"
      >
        <label htmlFor="ws-query" className="sr-only">Research question</label>
        <textarea
          id="ws-query"
          ref={inputRef}
          name="query"
          rows={2}
          maxLength={1000}
          autoComplete="off"
          className="ws-query"
          placeholder="e.g. How does a Taiwan fab disruption propagate to NVDA, AAPL and ASML?…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void run(query);
            }
          }}
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[12px] text-subtle">Enter to run · Shift+Enter for a new line · <kbd className="ws-kbd">/</kbd> to focus</p>
          <button type="submit" className="ws-btn-primary" disabled={running || query.trim().length < 3}>
            {running ? "Researching…" : "Run research"}
          </button>
        </div>
      </form>

      {!art && !running ? (
        <>
          <ul className="mt-5 space-y-1.5" aria-label="Sample questions">
            {SAMPLE_QUERIES.map((q) => (
              <li key={q}>
                <button type="button" className="ws-nav w-full text-left text-[13px]" onClick={() => { setQuery(q); void run(q); }}>
                  {q}
                </button>
              </li>
            ))}
          </ul>
          <ScenarioSwitcher />
        </>
      ) : null}

      <Stepper running={running} art={art} />
      {error ? (
        <p role="alert" className="ws-banner ws-banner-neg mt-4">
          {error.message}
          {error.requestId ? <span className="num block text-[11px]">Request ID {error.requestId}</span> : null}
        </p>
      ) : null}
      {art ? <ResearchAnswer art={art} /> : null}
      {art ? <ScenarioSwitcher /> : null}
    </div>
  );
}
