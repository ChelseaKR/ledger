import { readFileSync } from "node:fs";
import { join } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Browser-real axe pass over ledger's canonical served pages.
 *
 * For each page we run axe-core under BOTH the light and dark colour schemes
 * (ledger honours `prefers-color-scheme`, so contrast must hold either way) and
 * assert there are **no WCAG-tagged violations**, regardless of axe's impact
 * label. Impact is triage metadata, not permission to merge an AA violation.
 *
 * The record routes are resolved at runtime from `/api/records` because a
 * record id is minted per seed, not fixed. One of the two seeded records carries
 * a content warning, which drives the CW-interstitial state.
 */

const SCHEMES = ["light", "dark"] as const;

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** Resolve a record id (preferring one with a content warning) from the API. */
async function recordIds(
  request: APIRequestContext,
  baseURL: string,
): Promise<{ warned: string; any: string }> {
  const res = await request.get(`${baseURL}/api/records`);
  expect(res.ok(), `GET /api/records -> ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as {
    records: Array<{ record_id: string; content_warnings?: string[] }>;
  };
  expect(body.records.length, "seeded archive should expose records").toBeGreaterThan(0);
  const warned =
    body.records.find((r) => (r.content_warnings ?? []).length > 0) ?? body.records[0];
  return { warned: warned.record_id, any: body.records[0].record_id };
}

/**
 * Navigate to `path`, then run axe under each colour scheme and assert no
 * WCAG-tagged violations. `label` names the page in failure output.
 */
async function auditPage(page: Page, path: string, label: string): Promise<void> {
  const response = await page.goto(path, { waitUntil: "networkidle" });
  expect(response, `no response for ${label} (${path})`).not.toBeNull();
  expect(response!.status(), `${label} (${path}) -> ${response!.status()}`).toBeLessThan(400);

  for (const scheme of SCHEMES) {
    await page.emulateMedia({ colorScheme: scheme });
    const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    const blocking = results.violations;
    const detail = blocking
      .map((v) => `  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`)
      .join("\n");
    expect(
      blocking,
      `${label} (${path}) @ ${scheme}: WCAG axe violations:\n${detail}`,
    ).toEqual([]);
  }
}

// Static, always-available canonical pages.
const STATIC_PAGES: Array<{ path: string; label: string }> = [
  { path: "/", label: "browse (home)" },
  { path: "/search?q=Thursday", label: "search + facets" },
  { path: "/contribute", label: "contribute form" },
  { path: "/about", label: "about" },
  { path: "/how-it-works", label: "how it works" },
];

for (const { path, label } of STATIC_PAGES) {
  test(`axe: ${label}`, async ({ page }) => {
    await auditPage(page, path, label);
  });
}

test("axe: record view — content-warning interstitial", async ({ page, request, baseURL }) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditPage(page, `/record/${warned}`, "record (CW interstitial)");
});

test("axe: record view — after proceeding past the warning", async ({ page, request, baseURL }) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditPage(page, `/record/${warned}?proceed=1`, "record (proceeded)");
});

// The steward console is deny-by-default and grant headers are HMAC-authenticated
// (FIX-02): a bare subject is rejected. serve_demo.py signs a token for the
// provisioned `steward-1` grant and hands it over through a gitignored sidecar
// file, so this audit exercises the real authenticated grant path. auditPage's
// status assertion doubles as the auth check: a rejected token would render the
// neutral 404, not the console.
const stewardToken = readFileSync(
  process.env.LEDGER_A11Y_TOKEN_FILE ?? join(__dirname, ".steward-token"),
  "utf-8",
).trim();

test.describe("steward console (provisioned grant)", () => {
  test.use({ extraHTTPHeaders: { "X-Ledger-Grant": stewardToken } });

  test("axe: steward console", async ({ page }) => {
    await auditPage(page, "/steward", "steward console");
  });
});
