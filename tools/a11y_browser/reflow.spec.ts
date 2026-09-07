import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

import { assertRenderedDirection, expectedDirection } from "./direction";

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
 *
 * `playwright.config.ts` runs this file under a left-to-right project and a
 * right-to-left one, because a 320px column is exactly where a mirrored layout comes
 * apart and until the RTL project existed this gate had only ever rendered `en`. The
 * spill check below reads both edges for the same reason: in a mirrored layout content
 * runs off the left, and a check that only watches the right edge would run under
 * `dir="rtl"` and see nothing.
 */

const REFLOW_VIEWPORT = { width: 320, height: 256 } as const;

/** Allow one CSS pixel of slack for sub-pixel layout rounding, not for overflow. */
const ROUNDING_SLACK_PX = 1;

test.use({ viewport: REFLOW_VIEWPORT });

type Overflow = { selector: string; edge: string; at: number; width: number };

/**
 * Assert `path` reflows into a 320px column: no document-level horizontal scroll,
 * and no element extending past the right edge.
 *
 * Reporting the offending elements rather than a bare boolean matters — "the page
 * scrolls sideways" is not something anyone can act on, and the whole point of a
 * gate is that the failure tells you what to fix.
 */
async function auditReflow(
  page: Page,
  testInfo: TestInfo,
  path: string,
  label: string,
): Promise<void> {
  const response = await page.goto(path, { waitUntil: "networkidle" });
  expect(response, `no response for ${label} (${path})`).not.toBeNull();
  expect(response!.status(), `${label} (${path}) -> ${response!.status()}`).toBeLessThan(400);
  await assertRenderedDirection(page, testInfo, label, path);

  const documentOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(
    documentOverflow,
    `${label} (${path}) scrolls horizontally at ${REFLOW_VIEWPORT.width}px: ` +
      `content is ${documentOverflow}px wider than the viewport (WCAG 2.2 SC 1.4.10 Reflow)`,
  ).toBeLessThanOrEqual(ROUNDING_SLACK_PX);

  const spilling = await spillingElements(page);

  const detail = spilling
    .map((o) => `  ${o.selector} — ${o.edge} edge at ${o.at}px, width ${o.width}px`)
    .join("\n");
  expect(
    spilling,
    `${label} (${path}): element(s) extend past a ${REFLOW_VIEWPORT.width}px viewport ` +
      `(WCAG 2.2 SC 1.4.10 Reflow):\n${detail}`,
  ).toEqual([]);
}

/**
 * Every element extending past either edge of the viewport, or spilling its own box.
 *
 * Split out of `auditReflow` so that it can be pointed at a page with a fault planted
 * in it. The left-edge branch below was added for the right-to-left project, and once
 * `.skip-link` stopped parking itself at `left: -9999px` there was nothing left in this
 * archive that trips it. A branch with nothing to catch is a branch nobody has watched
 * catch anything, and the test at the bottom of this file plants one on each edge so
 * both are known to fire.
 */
async function spillingElements(page: Page): Promise<Overflow[]> {
  const slackFromCaller = ROUNDING_SLACK_PX;
  return page.evaluate((slack) => {
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
    // A page scrolls in its inline direction, so the edge that matters is the inline
    // END one: the right in `ltr`, the LEFT in `rtl`. Reading only `box.right` is what
    // a left-to-right-only harness gets away with, and it is why this check saw nothing
    // when the right-to-left project was first pointed at it.
    //
    // Content parked past the inline START edge is deliberately not reported. That is
    // the off-screen-until-focused idiom, it creates no scroll in either direction, and
    // flagging it would be flagging a conforming pattern. The distinction is not
    // cosmetic: ledger's own `.skip-link` sat at `left: -9999px`, which is harmlessly
    // off the start edge under `ltr` and 9,999 pixels of real horizontal scroll under
    // `rtl`, and only a direction-aware edge tells those two apart.
    const rtl = getComputedStyle(document.documentElement).direction === "rtl";
    const out: Array<{ selector: string; edge: string; at: number; width: number }> = [];
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      const box = el.getBoundingClientRect();
      // Zero-area nodes are collapsed or hidden and cannot be what a reader is
      // scrolling to reach.
      if (box.width === 0 || box.height === 0) continue;
      if (insideScroller(el)) continue;
      // Two distinct shapes of the same failure, and the second is the sneaky one:
      //   * the element's BOX runs past the inline end of the viewport; or
      //   * the box fits but its CONTENT does not (`scrollWidth > clientWidth` with
      //     `overflow-x: visible`) — a long unbreakable URL inside a correctly
      //     sized paragraph. Nothing looks wrong in a layout inspector, yet the
      //     text spills out and the page scrolls. Checking only bounding boxes
      //     misses it entirely.
      const pastInlineEnd = rtl ? box.left < -slack : box.right > limit;
      const contentSpills =
        getComputedStyle(el).overflowX === "visible" && el.scrollWidth > el.clientWidth + slack;
      if (pastInlineEnd) {
        out.push({
          selector: describe(el),
          edge: rtl ? "left" : "right",
          at: Math.round(rtl ? box.left : box.right),
          width: Math.round(box.width),
        });
      } else if (contentSpills) {
        out.push({
          selector: describe(el),
          edge: "content",
          at: Math.round(box.left + el.scrollWidth),
          width: Math.round(el.scrollWidth),
        });
      }
    }
    // Deduplicate: an overflowing child usually drags every ancestor with it, and
    // a hundred repetitions of the same fact is not a hundred findings.
    const seen = new Set<string>();
    return out.filter((o) => (seen.has(o.selector) ? false : (seen.add(o.selector), true)));
  }, slackFromCaller);
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
  test(`reflow @320px: ${label}`, async ({ page }, testInfo) => {
    await auditReflow(page, testInfo, path, label);
  });
}

test("reflow @320px: record view — content-warning interstitial", async (
  { page, request, baseURL },
  testInfo,
) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditReflow(page, testInfo, `/record/${warned}`, "record (CW interstitial)");
});

test("reflow @320px: record view — after proceeding past the warning", async (
  { page, request, baseURL },
  testInfo,
) => {
  const { warned } = await recordIds(request, baseURL!);
  await auditReflow(page, testInfo, `/record/${warned}?proceed=1`, "record (proceeded)");
});

// Same provisioned-grant path as axe.spec.ts: the steward console is
// deny-by-default and needs a signed token, written out by serve_demo.py.
const stewardToken = readFileSync(
  process.env.LEDGER_A11Y_TOKEN_FILE ?? join(__dirname, ".steward-token"),
  "utf-8",
).trim();

test.describe("steward console (provisioned grant)", () => {
  test.use({ extraHTTPHeaders: { "X-Ledger-Grant": stewardToken } });

  test("reflow @320px: steward console", async ({ page }, testInfo) => {
    await auditReflow(page, testInfo, "/steward", "steward console");
  });
});

// A gate nobody has watched refuse is a gate nobody knows can refuse, and the inline
// edge branch above has nothing left in this archive that trips it: the pages pass.
// So a fault is planted past the inline END edge of whichever direction is running,
// on a page asserted clean first, and it is required to come back named. A second
// element is planted past the inline START edge and is required NOT to come back,
// because that is the off-screen-until-focused idiom and reporting it would be
// reporting a conforming pattern as a failure.
//
// `bypassCSP` is load-bearing rather than convenience. `server._send` sets a
// conservative Content-Security-Policy with no `unsafe-inline`, so Chromium silently
// discards a `style` attribute written from script: the first draft of this test
// created both elements, saw the scan's own querySelectorAll find them, and measured
// them sitting at left 0 with the page's default width, spilling past nothing at all.
// A planted fault that does not land proves nothing, so the context is allowed to
// apply the style and the geometry is asserted below before anything is concluded.
test.describe("the spill check's own fault injection", () => {
  test.use({ bypassCSP: true });

  test("reflow @320px: the spill check reports a fault past the inline end and not the start", async ({
    page,
  }, testInfo) => {
    await page.goto("/about", { waitUntil: "networkidle" });
    await assertRenderedDirection(page, testInfo, "fault injection", "/about");
    expect(
      await spillingElements(page),
      "/about is already spilling, so planting a fault on it would prove nothing",
    ).toEqual([]);

    const rtl = expectedDirection(testInfo) === "rtl";
    const planted = await page.evaluate(
      ({ width, rtl }) => {
        const geometry: Record<string, { left: number; width: number }> = {};
        const positions: Array<[string, number]> = [
          ["planted-past-the-inline-end", rtl ? -400 : width + 40],
          ["planted-past-the-inline-start", rtl ? width + 40 : -400],
        ];
        for (const [id, left] of positions) {
          const el = document.createElement("div");
          el.id = id;
          el.textContent = id;
          el.setAttribute(
            "style",
            `position: absolute; top: 0; left: ${left}px; width: 200px; height: 20px;`,
          );
          document.body.append(el);
          const box = el.getBoundingClientRect();
          geometry[id] = { left: box.left, width: box.width };
        }
        return geometry;
      },
      { width: REFLOW_VIEWPORT.width, rtl },
    );

    // The faults landed where they were aimed, rather than having their style dropped
    // and sitting harmlessly in the flow.
    expect(planted["planted-past-the-inline-end"]).toEqual({
      left: rtl ? -400 : REFLOW_VIEWPORT.width + 40,
      width: 200,
    });
    expect(planted["planted-past-the-inline-start"]).toEqual({
      left: rtl ? REFLOW_VIEWPORT.width + 40 : -400,
      width: 200,
    });

    const found = await spillingElements(page);
    const selectors = found.map((o) => o.selector);
    expect(selectors, `the scan reported ${JSON.stringify(found)}`).toContain(
      "div#planted-past-the-inline-end",
    );
    expect(selectors).not.toContain("div#planted-past-the-inline-start");
    expect(found.find((o) => o.selector === "div#planted-past-the-inline-end")!.edge).toBe(
      rtl ? "left" : "right",
    );
  });
});
