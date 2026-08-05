import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * WCAG 2.2 SC 1.4.10 Reflow (AA) — the one accessibility criterion that axe
 * cannot check for us.
 *
 * axe-core evaluates the DOM it is handed; it has no opinion about the viewport
 * that DOM was laid out in. So the existing `axe.spec.ts` pass can be perfectly
 * green on a page that, at 320 CSS pixels, forces a reader to scroll sideways to
 * read every single line. That is not a cosmetic complaint. It is the difference
 * between a usable and an unusable archive for:
 *
 *   * anyone at 400% browser zoom on a 1280px screen — 1280 / 4 = 320, which is
 *     exactly where this number comes from and why the SC picks it;
 *   * anyone on a small phone, which for a community archive is most people.
 *
 * SC 1.4.10 permits horizontal scrolling only for content that genuinely needs a
 * second dimension (a wide data table, a map). ledger's browse, record, search,
 * and form pages are prose and lists; none of them qualify. So the assertion is
 * absolute: at 320 x 256 the document must not scroll horizontally, and no
 * individual element may spill past the viewport.
 *
 * 320 x 256 is the SC's own reference: 1280 x 1024 at 400% zoom.
 */

const REFLOW_VIEWPORT = { width: 320, height: 256 } as const;

/** Allow one CSS pixel of slack for sub-pixel layout rounding, not for overflow. */
const ROUNDING_SLACK_PX = 1;

test.use({ viewport: REFLOW_VIEWPORT });

type Overflow = { selector: string; right: number; width: number };

/**
 * Assert `path` reflows into a 320px column: no document-level horizontal scroll,
 * and no element extending past the right edge.
 *
 * Reporting the offending elements rather than a bare boolean matters — "the page
 * scrolls sideways" is not something anyone can act on, and the whole point of a
 * gate is that the failure tells you what to fix.
 */
async function auditReflow(page: Page, path: string, label: string): Promise<void> {
  const response = await page.goto(path, { waitUntil: "networkidle" });
  expect(response, `no response for ${label} (${path})`).not.toBeNull();
  expect(response!.status(), `${label} (${path}) -> ${response!.status()}`).toBeLessThan(400);

  const documentOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(
    documentOverflow,
    `${label} (${path}) scrolls horizontally at ${REFLOW_VIEWPORT.width}px: ` +
      `content is ${documentOverflow}px wider than the viewport (WCAG 2.2 SC 1.4.10 Reflow)`,
  ).toBeLessThanOrEqual(ROUNDING_SLACK_PX);

  const spilling: Overflow[] = await page.evaluate((slack) => {
    const limit = document.documentElement.clientWidth + slack;
    const describe = (el: Element): string => {
      const id = el.id ? `#${el.id}` : "";
      const cls =
        typeof el.className === "string" && el.className
          ? `.${el.className.trim().split(/\s+/).join(".")}`
          : "";
      return `${el.tagName.toLowerCase()}${id}${cls}`;
    };
    /**
     * SC 1.4.10 exempts content that requires a second dimension — a wide data
     * table is the canonical example. ledger's record table already scrolls
     * inside its own `overflow-x: auto` box on narrow viewports, which is the
     * conforming pattern, so flagging its cells would be flagging the fix. What
     * the SC forbids is the *page* scrolling; a self-contained scroller does not.
     */
    const insideScroller = (el: Element): boolean => {
      for (let node = el.parentElement; node && node !== document.body; node = node.parentElement) {
        const overflowX = getComputedStyle(node).overflowX;
        if (overflowX === "auto" || overflowX === "scroll") return true;
      }
      return false;
    };
    const out: Array<{ selector: string; right: number; width: number }> = [];
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      const box = el.getBoundingClientRect();
      // Zero-area nodes are collapsed or hidden and cannot be what a reader is
      // scrolling to reach.
      if (box.width === 0 || box.height === 0) continue;
      if (insideScroller(el)) continue;
      // Two distinct shapes of the same failure, and the second is the sneaky one:
      //   * the element's BOX is wider than the viewport; or
      //   * the box fits but its CONTENT does not (`scrollWidth > clientWidth` with
      //     `overflow-x: visible`) — a long unbreakable URL inside a correctly
      //     sized paragraph. Nothing looks wrong in a layout inspector, yet the
      //     text spills out and the page scrolls. Checking only bounding boxes
      //     misses it entirely.
      const contentSpills =
        getComputedStyle(el).overflowX === "visible" && el.scrollWidth > el.clientWidth + slack;
      if (box.right > limit || contentSpills) {
        out.push({
          selector: describe(el),
          right: Math.round(Math.max(box.right, box.left + el.scrollWidth)),
          width: Math.round(Math.max(box.width, el.scrollWidth)),
        });
      }
    }
    // Deduplicate: an overflowing child usually drags every ancestor with it, and
    // a hundred repetitions of the same fact is not a hundred findings.
    const seen = new Set<string>();
    return out.filter((o) => (seen.has(o.selector) ? false : (seen.add(o.selector), true)));
  }, ROUNDING_SLACK_PX);

  const detail = spilling
    .map((o) => `  ${o.selector} — right edge ${o.right}px, width ${o.width}px`)
    .join("\n");
  expect(
    spilling,
    `${label} (${path}): element(s) extend past a ${REFLOW_VIEWPORT.width}px viewport ` +
      `(WCAG 2.2 SC 1.4.10 Reflow):\n${detail}`,
  ).toEqual([]);
}

/** Resolve a record id (preferring one with a content warning) from the API. */
async function recordIds(
  request: APIRequestContext,
  baseURL: string,
): Promise<{ warned: string }> {
  const res = await request.get(`${baseURL}/api/records`);
  expect(res.ok(), `GET /api/records -> ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as {
    records: Array<{ record_id: string; content_warnings?: string[] }>;
  };
  expect(body.records.length, "seeded archive should expose records").toBeGreaterThan(0);
  const warned =
    body.records.find((r) => (r.content_warnings ?? []).length > 0) ?? body.records[0];
  return { warned: warned.record_id };
}

// The same canonical surface axe.spec.ts audits, so the two engines cover one set
// of pages and a page cannot be quietly exempt from one of them.
const STATIC_PAGES: Array<{ path: string; label: string }> = [
  { path: "/", label: "browse (home)" },
  { path: "/search?q=Thursday", label: "search + facets" },
  { path: "/contribute", label: "contribute form" },
  { path: "/about", label: "about" },
  { path: "/how-it-works", label: "how it works" },
];

for (const { path, label } of STATIC_PAGES) {
  test(`reflow @320px: ${label}`, async ({ page }) => {
    await auditReflow(page, path, label);
  });
}

test("reflow @320px: record view — content-warning interstitial", async ({
  page,
  request,
  baseURL,
}) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditReflow(page, `/record/${warned}`, "record (CW interstitial)");
});

test("reflow @320px: record view — after proceeding past the warning", async ({
  page,
  request,
  baseURL,
}) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditReflow(page, `/record/${warned}?proceed=1`, "record (proceeded)");
});

// Same provisioned-grant path as axe.spec.ts: the steward console is
// deny-by-default and needs a signed token, written out by serve_demo.py.
const stewardToken = readFileSync(
  process.env.LEDGER_A11Y_TOKEN_FILE ?? join(__dirname, ".steward-token"),
  "utf-8",
).trim();

test.describe("steward console (provisioned grant)", () => {
  test.use({ extraHTTPHeaders: { "X-Ledger-Grant": stewardToken } });

  test("reflow @320px: steward console", async ({ page }) => {
    await auditReflow(page, "/steward", "steward console");
  });
});
