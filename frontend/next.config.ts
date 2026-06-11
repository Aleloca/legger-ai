import type { NextConfig } from "next";

/**
 * Backend FastAPI base URL. Override with the BACKEND_URL env var
 * (e.g. in Docker: BACKEND_URL=http://backend:8000).
 */
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Self-contained server at .next/standalone for the Docker image (H1).
  // NOTE: next.config is serialized into the standalone server at build
  // time, so the BACKEND_URL above must be set when `next build` runs
  // (the frontend Dockerfile bakes it as a build arg).
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/backend/:path*",
        destination: `${BACKEND_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
