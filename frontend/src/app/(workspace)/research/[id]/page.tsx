"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";
import useSWR from "swr";
import { ApiAuthError, ApiNotFoundError } from "@/lib/api";
import { researchApi, type Artifact } from "@/lib/research";
import { ResearchView } from "@/components/workspace/ResearchView";

export default function ResearchSessionPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, isLoading } = useSWR<Artifact>(id ? ["session", id] : null, () => researchApi.session(id), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });

  if (isLoading) return <p className="ws-empty">Loading research session…</p>;
  if (error) {
    const msg =
      error instanceof ApiAuthError
        ? "403 — this research session belongs to another workspace."
        : error instanceof ApiNotFoundError
          ? "This research session does not exist."
          : "Could not load the session. Is the API running?";
    return (
      <div role="alert" className="ws-banner ws-banner-neg">
        {msg} <Link className="ws-link ml-2" href="/research">Start new research</Link>
      </div>
    );
  }
  return (
    <Suspense fallback={null}>
      <ResearchView key={id} initial={data} />
    </Suspense>
  );
}
