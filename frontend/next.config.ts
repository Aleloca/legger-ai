import type { NextConfig } from "next";

/**
 * Backend FastAPI base URL. Override with the BACKEND_URL env var
 * (e.g. in Docker: BACKEND_URL=http://backend:8000).
 */
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
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
