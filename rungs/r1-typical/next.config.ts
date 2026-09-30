import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Latency Ladder project-wide decision (docs/phase-a/README.md §1.3-1.4): every web rung is
  // served cross-origin isolated so all rungs get 5 µs timers and uncoarsened presentation
  // times. A typical production Next.js app would NOT send these headers.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
        ],
      },
    ];
  },
};

export default nextConfig;
