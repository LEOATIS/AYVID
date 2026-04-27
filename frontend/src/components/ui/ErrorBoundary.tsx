"use client";

/**
 * Top-level error boundary for the console.
 *
 * React's render-time and lifecycle errors have a narrow recovery
 * surface: once a subtree throws, every subsequent render collapses
 * until the boundary remounts the tree. In production we want:
 *
 *   1. A visible, non-leaking error panel instead of a white screen.
 *   2. The `X-Request-ID` of any in-flight API call that surfaced
 *      the error, so a user support ticket can tie back to backend
 *      logs. We read it off the error instance when the error is
 *      an `ApiError`; plain JS errors get a synthetic id from
 *      `crypto.randomUUID`.
 *   3. A "Reload" button that remounts the tree — the only safe
 *      recovery for a crashed component subtree.
 *
 * NOTE: React 19 still requires a class component for error
 * boundaries. There is no function-component equivalent in the
 * stable API; `useErrorBoundary` lives in experimental.
 */

import React from "react";

import { ApiError } from "@/lib/api";

interface ErrorBoundaryProps {
  /** Subtree to protect. */
  children: React.ReactNode;
  /** Optional fallback override. When provided, we skip the default UI. */
  fallback?: (err: Error, reset: () => void, requestId: string | null) => React.ReactNode;
  /** Called with (error, errorInfo, requestId) on every caught throw. */
  onError?: (err: Error, info: React.ErrorInfo, requestId: string | null) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
  requestId: string | null;
}

export class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null, requestId: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    // This runs during the render-phase error handling — we cannot
    // touch refs or fire effects here. Capture the request-id off the
    // thrown value so the fallback UI can display it.
    const requestId =
      error instanceof ApiError
        ? error.requestId
        : (typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : null);
    return { error, requestId };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Side effects belong in componentDidCatch, not
    // getDerivedStateFromError. This is the hook for logging /
    // shipping the error to a tracker.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
    this.props.onError?.(error, info, this.state.requestId);
  }

  private _reset = () => {
    this.setState({ error: null, requestId: null });
  };

  render() {
    const { error, requestId } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this._reset, requestId);
    }
    return <DefaultFallback error={error} requestId={requestId} onReset={this._reset} />;
  }
}

function DefaultFallback({
  error,
  requestId,
  onReset,
}: {
  error: Error;
  requestId: string | null;
  onReset: () => void;
}) {
  const isApi = error instanceof ApiError;
  const title = isApi ? `API error (${(error as ApiError).status || "network"})` : error.name;
  const summary = error.message || "An unexpected error occurred.";
  return (
    <div
      role="alert"
      className="mx-auto my-10 max-w-xl rounded-md border border-red-300/30 bg-red-500/5 p-5"
    >
      <h2 className="mb-1 text-base font-semibold text-red-200">{title}</h2>
      <p className="mb-3 text-sm leading-relaxed text-red-100/80">{summary}</p>
      {requestId && (
        <p className="mb-3 font-mono text-xs text-red-100/60">
          request_id: <span className="select-all">{requestId}</span>
        </p>
      )}
      <button
        type="button"
        onClick={onReset}
        className="rounded-md border border-red-300/40 bg-red-500/20 px-3 py-1.5 text-sm text-red-50 hover:bg-red-500/30"
      >
        Reload this panel
      </button>
    </div>
  );
}
