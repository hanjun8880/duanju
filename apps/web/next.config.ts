import type { NextConfig } from "next";

// Browser only ever talks to this origin; the server proxies to FastAPI, so no vendor key or URL reaches the client.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // planning a whole novel is one long LLM call; the default proxy timeout would cut it off
  experimental: { proxyTimeout: 15 * 60 * 1000 },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` },
      { source: "/media/:path*", destination: `${backend}/media/:path*` },
    ];
  },
};

export default nextConfig;
