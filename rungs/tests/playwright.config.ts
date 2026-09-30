// Parity checks for the R1 rungs against their PRODUCTION builds (next start / vite preview).
// Build first: npm run -w @latency-ladder/rung-tests build:rungs
// Chromium is preinstalled (Playwright 1.56.1 = chromium-1194); never run `playwright install`.
// Correctness/parity only: never measure latency through Playwright-launched Chrome (CDP input,
// automation flags), see docs/phase-a/README.md §0.6 and 05 §4. The @timing spec is "rough".
import { defineConfig } from '@playwright/test';

process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers';

const NEXT_PORT = Number(process.env.LADDER_NEXT_PORT ?? 3101);
const VITE_PORT = Number(process.env.LADDER_VITE_PORT ?? 3102);

export default defineConfig({
  testDir: './specs',
  outputDir: './out/test-results',
  timeout: 15 * 60_000,
  expect: { timeout: 120_000 },
  fullyParallel: true,
  workers: Number(process.env.LADDER_WORKERS ?? 3),
  reporter: [['list'], ['json', { outputFile: './out/results.json' }]],
  use: {
    channel: 'chromium', // full Chromium (new headless), not the headless shell
    viewport: { width: 1280, height: 800 },
    actionTimeout: 300_000,
    navigationTimeout: 300_000,
  },
  projects: [
    { name: 'r1-typical', use: { baseURL: `http://localhost:${NEXT_PORT}` } },
    { name: 'r1-vite', use: { baseURL: `http://localhost:${VITE_PORT}` } },
  ],
  webServer: [
    {
      command: `npx next start -p ${NEXT_PORT}`,
      cwd: '../r1-typical',
      url: `http://localhost:${NEXT_PORT}/ladder/marker.json`,
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: `npx vite preview --port ${VITE_PORT} --strictPort`,
      cwd: '../r1-vite',
      url: `http://localhost:${VITE_PORT}/ladder/marker.json`,
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
