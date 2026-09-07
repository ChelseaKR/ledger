import { defineConfig, devices } from "@playwright/test";

import { RTL_LOCALE } from "./direction";

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
    // The same pages, negotiated into Arabic so the server renders
    // `<html lang="ar" dir="rtl">` and the browser mirrors the layout. Until this
    // project existed, the stock `Desktop Chrome` device sent `Accept-Language: en-US`
    // and every run of these two specs had rendered left-to-right, so the direction
    // machinery ledger ships (`i18n._RTL_LANGS`, `text_direction`, `<html dir>`) had
    // never been drawn by a renderer. `direction.ts` asserts the document really did
    // come back `rtl`, because a locale that failed to arrive would otherwise leave
    // every test here passing on a second left-to-right run.
    //
    // axe and reflow only: both are layout-sensitive and neither can see a direction it
    // was not rendered in. `keyboard.spec.ts` traverses in DOM order, which mirroring
    // does not change, so a second pass of it would add runtime and no coverage.
    {
      name: "chromium-rtl",
      testMatch: /(axe|reflow)\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"], locale: RTL_LOCALE },
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
