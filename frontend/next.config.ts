import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// `next dev` gets its own build dir: running `next build` while a dev server
// is up otherwise overwrites `.next` and the dev server serves stale routes (404s).
export default function config(phase: string): NextConfig {
  return {
    reactStrictMode: true,
    devIndicators: false,
    output: "standalone",
    distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
    async rewrites() {
      const apiBase = process.env.AYVID_API_BASE ?? "http://localhost:8000";
      return [
        { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
        { source: "/sse/:path*", destination: `${apiBase}/sse/:path*` },
      ];
    },
  };
}
