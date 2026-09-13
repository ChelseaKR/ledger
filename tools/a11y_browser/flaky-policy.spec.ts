// This harness's Playwright config must fail the run when a test passed only
// on its retry.
//
// `playwright.config.ts` retries once. Playwright reports a test that failed
// and then passed on that retry as `flaky`, and exits 0 on flaky unless
// `failOnFlakyTests` is set — the runner's verdict is
// `... || hasFailedTests || config.failOnFlakyTests && hasFlakyTests`. Without
// the key, the retry converts an intermittent failure (a race, an unawaited
// promise, a cold-start timeout) into a green check that records nothing but a
// log line. See #223.
//
// This IMPORTS the config the way Playwright reads it rather than matching its
// text, so a key that is commented out, misspelt, or set to `false` cannot
// satisfy it. It runs inside the harness, where the Playwright dependency that
// resolves the config already exists — a check that lived in the Python suite
// would have no way to load a TypeScript config.
import { expect, test } from "@playwright/test";

import config from "./playwright.config";

test("the config that governs this run fails it when a test needed a retry", () => {
  // Vacuity guard. The policy assertion below reads keys off `config`, and a
  // missing key reads as `undefined` — so an import that resolved to something
  // other than this file (an empty object, a changed module shape) would
  // satisfy it by appearing to have no retries rather than by being safe.
  // `testDir` is checked because this config always sets it and Playwright's
  // loader does not synthesize it: loading `export default {}` yields an object
  // carrying only the `metadata` key the loader adds, so counting keys would
  // not have caught that case.
  expect(config, "playwright.config.ts did not export a config object").toBeTruthy();
  expect(
    typeof config.testDir,
    "playwright.config.ts no longer sets `testDir`, so this import may not be the config Playwright ran; " +
      "the assertion below would be reading undefined off the wrong object",
  ).toBe("string");

  const retries = config.retries ?? 0;
  expect(
    retries === 0 || config.failOnFlakyTests === true,
    `playwright.config.ts retries ${retries} time(s) but does not set failOnFlakyTests: true, ` +
      "so a test that passes only on its retry reads as green",
  ).toBe(true);
});
