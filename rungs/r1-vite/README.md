# R1 "Typical" (Vite)

The Next-vs-Vite control for R1 (spec open question; 05 §3.2, phase-a §0.7). Same `Palette` as
`rungs/r1-typical/`, same shadcn/cmdk code, built with Vite + React instead of Next.js. After the
first render both run identical React and cmdk code, so the two should differ mainly in cold start
(RSC payload, router, hydration) and memory. Measure both; if Scenario A differs by less than the
noise band, Next vs Vite matters only for Scenario D.

## Build, run, test

```sh
npm run -w @latency-ladder/rung-tests install:rungs   # npm ci in both R1 variants
npm run -w @latency-ladder/rung-tests build:rungs     # prepare dataset/probe, next build, vite build
cd rungs/r1-vite && npx vite preview --port 3102 --strictPort
#   http://localhost:3102/?items=/dataset/10k/items.json   (default /dataset/items.json = 50k)
npm run -w @latency-ladder/rung-tests test            # tests both variants
```

`vite preview` serves `dist/`, which Vite fills from `public/` at build time. After regenerating
the dataset, run `node rungs/scripts/prepare.mjs` again (it refreshes `dist/` too) or rebuild.

## How it was made (typical)

1. `npx create-vite@latest r1-vite --template react-ts` (create-vite 9.2.1: Vite 8, React, oxlint).
2. shadcn's Vite installation steps: `tailwindcss` + `@tailwindcss/vite`, `@import "tailwindcss"`,
   the `@/*` path alias in `tsconfig*.json` and `vite.config.ts` (TypeScript 6 rejects `baseUrl`,
   so only `paths` is set), then `npx shadcn@4.21.0 init -b base -p nova` (= style `base-nova`, the
   same as the Next variant's default) and `npx shadcn@4.21.0 add command`.
3. `cmdk` pinned to **1.1.1**. Dark mode: the `ThemeProvider` from shadcn's "Dark mode > Vite"
   docs (trimmed to "follow the OS unless a theme was stored").
4. `src/App.tsx` renders the same layout as the Next page; `src/main.tsx` is the template's
   (`StrictMode`, which does nothing in production builds).

Shared files are **duplicated, not imported** (each variant is a standalone app with its own
lockfile, as a team would have), and kept identical by `rungs/scripts/check-sync.mjs` (run by the
test script): `components/palette.tsx`, `latency-marker.tsx`, `ladder-types.ts`, `lib/utils.ts`
byte-for-byte; `components/ui/*.tsx` ignoring the leading `"use client"` that shadcn only emits for
RSC projects. The `"use client"` in the shared files is a no-op under Vite.

## Not typical

Same list as the Next variant: COOP/COEP headers (`server.headers` and `preview.headers` in
`vite.config.ts`; a typical Vite app would not send them), `/ladder/probe.js` (a plain `<script>`
in `index.html`, harness-owned), the `LatencyMarker` hook and the test hooks in `palette.tsx`.
`vite preview` is Vite's static preview server for the production build, not a production CDN;
headers and static serving are all it does here.

## Pinned versions

From `package-lock.json`: vite **8.3.1**, react / react-dom **19.3.0** (the Next variant renders
with Next's vendored 19.3.0-canary), cmdk **1.1.1**, tailwindcss 4.3.3, @tailwindcss/vite 4.3.3,
@vitejs/plugin-react 6.1.1, @base-ui/react 1.8.0, lucide-react 1.49.0, cn 0.4.0,
@fontsource-variable/geist 5.3.0, shadcn 4.21.0, TypeScript 6.0.3.
