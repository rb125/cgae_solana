import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // In Vercel, ensure BACKEND_API_URL is set to https://rb512-cgae-solana.hf.space
    const backendUrl = (process.env.BACKEND_API_URL || "http://localhost:8000").replace(/\/$/, "");
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
