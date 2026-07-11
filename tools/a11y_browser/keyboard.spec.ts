import { expect, test, type Page } from "@playwright/test";

/**
 * Keyboard-only traversal of the contribute form.
 *
 * axe cannot judge operability: whether a keyboard user can actually reach every
 * control and see where focus is. This spec Tabs through the page with no pointer
 * and asserts that (a) the key form fields and the submit button all receive
 * focus, and (b) each shows a visible focus indicator (ledger's 3px
 * `:focus-visible` outline — WCAG 2.4.7). If focus were trapped or a control were
 * unreachable, the traversal would never reach submit and the test fails.
 */

interface ActiveDescriptor {
  tag: string;
  type: string | null;
  id: string;
  name: string | null;
  value: string | null;
  /** True when the focused element paints a non-zero focus outline. */
  hasVisibleFocus: boolean;
}

async function describeActive(page: Page): Promise<ActiveDescriptor> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) {
      return {
        tag: "",
        type: null,
        id: "",
        name: null,
        value: null,
        hasVisibleFocus: false,
      };
    }
    const style = getComputedStyle(el);
    const outlineWidth = parseFloat(style.outlineWidth || "0");
    const hasVisibleFocus =
      (style.outlineStyle !== "none" && outlineWidth > 0) ||
      (style.boxShadow !== "none" && style.boxShadow !== "");
    return {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type"),
      id: el.id || "",
      name: el.getAttribute("name"),
      value: el.getAttribute("value"),
      hasVisibleFocus,
    };
  });
}

test("keyboard: contribute form is fully reachable with a visible focus ring", async ({
  page,
}) => {
  await page.goto("/contribute", { waitUntil: "networkidle" });

  // Start from the top of the document so traversal begins at the skip link, as a
  // real keyboard user's first Tab would.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.focus();
  });

  const seenIds = new Set<string>();
  const focusRingMisses: string[] = [];
  let reachedSubmit = false;

  // Bounded walk: the form has well under 40 focusable stops; the cap just
  // prevents an infinite loop if focus were trapped.
  for (let i = 0; i < 40 && !reachedSubmit; i++) {
    await page.keyboard.press("Tab");
    const active = await describeActive(page);
    if (active.tag === "") continue;

    if (active.id) seenIds.add(active.id);

    // Interactive stops must advertise focus visibly. Anchors, inputs, textareas
    // and buttons all inherit the global :focus-visible ring under keyboard nav.
    const interactive = ["a", "input", "textarea", "button", "select"].includes(active.tag);
    if (interactive && !active.hasVisibleFocus) {
      focusRingMisses.push(active.id || `${active.tag}[${active.type ?? ""}]`);
    }

    if (active.tag === "button" && active.name === "action" && active.value === "submit") {
      reachedSubmit = true;
    }
  }

  // The required fields must be individually reachable by keyboard.
  expect(seenIds, "title field should be reachable by Tab").toContain("title");
  expect(seenIds, "account/story field should be reachable by Tab").toContain("account");

  // The submit button must be reachable without a pointer.
  expect(reachedSubmit, "submit button should be reachable by keyboard alone").toBeTruthy();

  // Every interactive stop along the way must have shown a visible focus ring.
  expect(
    focusRingMisses,
    `controls reached without a visible focus indicator: ${focusRingMisses.join(", ")}`,
  ).toEqual([]);
});
