/** Starter research questions shared by the workspace and the marketing pages (plain module: server-safe). */
export const CORE_QUERY =
  "What is NVDA's supply-chain exposure to TSMC and how do its recent margins, volatility regime, and China restrictions affect a long-term thesis?";

export const OUTAGE_QUERY =
  "How does the Microsoft Azure and M365 cascading outage propagate across cloud, observability, and cybersecurity stocks (MSFT, GOOGL, AMZN, DDOG, CRWD, NET)?";

export const PACKAGING_QUERY = "How does a TSMC CoWoS packaging bottleneck propagate to NVDA, AVGO and MSFT?";

export const MARGIN_QUERY = "Analyze NVDA gross margin trajectory, customer concentration, and key invalidation risks";

/** Link that opens the workspace and runs the question immediately. */
export function researchHref(q: string, extra = ""): string {
  return `/research?q=${encodeURIComponent(q)}&run=1${extra}`;
}
