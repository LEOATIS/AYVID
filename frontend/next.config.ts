import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    const apiBase = process.env.AYVID_API_BASE ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      { source: "/sse/:path*", destination: `${apiBase}/sse/:path*` },
    ];
  },
};

export default nextConfig;
