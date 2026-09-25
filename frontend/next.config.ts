import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy /api/* to the FastAPI service so the browser only talks to one origin.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  experimental: { proxyTimeout: 120_000 }, // agent answers can take a while
};

export default nextConfig;
