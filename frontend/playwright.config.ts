import { defineConfig } from "@playwright/test";

/**
 * Smoke flow against the REAL backend (real embeddings, real Groq). Start it first:
 *   cd backend && python -m scripts.serve_local        (http://localhost:8000)
 * then `npm run test:e2e`. The browser is the locally installed Chrome, so nothing is downloaded;
 * set PW_CHANNEL=msedge (or an empty value to use Playwright's own Chromium) to change that.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 90_000 },
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: process.env.PW_CHANNEL ?? "chrome",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : { command: "npm run dev", url: "http://localhost:3000", reuseExistingServer: true, timeout: 120_000 },
});
