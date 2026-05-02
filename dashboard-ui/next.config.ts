import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const backendUrl = process.env.BACKEND_API_URL || "http://localhost:8000";
    return [
      {
        source: "/api/state",
        destination: `${backendUrl}/get_api_state`,
      },
      {
        source: "/api/agents",
        destination: `${backendUrl}/get_api_agents`,
      },
      {
        source: "/api/trades",
        destination: `${backendUrl}/get_api_trades`,
      },
      {
        source: "/api/events",
        destination: `${backendUrl}/get_api_events`,
      },
      {
        source: "/api/timeseries",
        destination: `${backendUrl}/get_api_timeseries`,
      },
    ];
  },
};

export default nextConfig;
