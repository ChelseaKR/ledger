import { defineConfig, devices } from "@playwright/test";

/**
 * Browser-real accessibility harness for ledger's served demo surface.
 *
 * This is CI/dev-only depth over the stdlib static gate
 * (`python -m ledger.accessibility_check web`): a headless Chromium drives the
 * actual pages the server renders and runs axe-core against them. It adds no
 * runtime dependency to the `ledger` package.
 *
 * The `webServer` block seeds a throwaway demo archive and runs `ledger serve`
 * via `serve_demo.py`, and Playwright waits for it to answer before the specs run
 * (and tears it down after). `baseURL` comes from `LEDGER_BASE_URL` so the same
 * specs can point at an already-running server in local iteration.
 */
const PORT = Number(process.env.LEDGER_A11Y_PORT ?? "8099");
const HOST = process.env.LEDGER_A11Y_HOST ?? "127.0.0.1";
const BASE_URL = process.env.LEDGER_BASE_URL ?? `http://${HOST}:${PORT}`;

export default defineConfig({
  testDir: ".",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // One cheap retry absorbs a rare cold-start/port flake without masking a real
  // regression, which reproduces on the retry too.
  retries: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Reuse an externally-started server when LEDGER_BASE_URL is set; otherwise
  // start the seed-and-serve helper ourselves.
  webServer: process.env.LEDGER_BASE_URL
    ? undefined
    : {
        command: "python -m serve_demo",
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          LEDGER_A11Y_HOST: HOST,
          LEDGER_A11Y_PORT: String(PORT),
        },
      },
});
