import { expect, type Page, type TestInfo } from "@playwright/test";

/**
 * Which base text direction each spec must be rendering in, and the assertion that it
 * actually was.
 *
 * Until this file existed, `axe.spec.ts` and `reflow.spec.ts` set no language anywhere.
 * Playwright's stock `Desktop Chrome` device sends `Accept-Language: en-US`, so
 * `ledger.server._lang()` negotiated `en` on every request and both gates had only ever
 * rendered `<html lang="en" dir="ltr">`. ledger ships en/es/fr/ar, `i18n._RTL_LANGS`
 * marks `ar` right-to-left, and `render._page` sets `<html dir>` from
 * `i18n.text_direction` — so the one layout that machinery exists for had never once
 * been drawn in a browser. RTL was asserted in `tests/test_i18n_rtl.py`, but only
 * against the string `_page()` returns, which cannot tell anyone whether a 320px column
 * still reflows or whether axe finds a violation once the layout mirrors.
 *
 * The `chromium-rtl` project in `playwright.config.ts` drives the same pages under
 * `locale: "ar"`. Setting the locale is not on its own something to trust. If it never
 * reached the server — a changed device default, a remembered `lang` cookie, a
 * negotiation that fell back to `en` — every RTL test would render left-to-right and
 * pass, and the suite would report coverage of a direction it had never exercised. That
 * is the failure this whole change exists to end, so it is asserted rather than
 * assumed: the direction the document actually declares is checked before any other
 * check runs, on every page, in both projects.
 */

/** The locale the right-to-left project drives, and the one place it is named. */
export const RTL_LOCALE = "ar";

/** The project in `playwright.config.ts` that audits a mirrored layout. */
export const RTL_PROJECT = "chromium-rtl";

/**
 * The direction each locale this harness drives must produce.
 *
 * A deliberately small mirror of `ledger.i18n._RTL_LANGS`, holding only what the
 * harness actually asks for. It is not a second implementation of the rule: if the
 * server and this table ever disagree about `ar`, the assertion below fails, which is
 * the outcome worth having.
 */
export const DIRECTION_BY_LOCALE: Readonly<Record<string, "ltr" | "rtl">> = {
  ar: "rtl",
};

/**
 * The direction the running project is *supposed* to be auditing, from its name.
 *
 * Read from the project's identity and deliberately not from its `locale`. The first
 * draft of this derived the expectation from `project.use.locale`, which made the whole
 * assertion circular: delete the locale from `playwright.config.ts` and
 * `expectedDirection` quietly answered "ltr", the page duly rendered ltr, and the
 * right-to-left project went green having audited nothing but a second English run.
 * A negative control caught it, which is what negative controls are for. A project
 * named for a direction has to produce that direction, whatever its configuration
 * happens to say.
 */
export function expectedDirection(testInfo: TestInfo): "ltr" | "rtl" {
  return testInfo.project.name === RTL_PROJECT ? "rtl" : "ltr";
}

/** The direction a locale is expected to produce, or `ltr` when it is not declared. */
export function directionOfLocale(locale: string | undefined): "ltr" | "rtl" {
  if (!locale) return "ltr";
  return DIRECTION_BY_LOCALE[locale.toLowerCase().split("-")[0]] ?? "ltr";
}

/**
 * Assert the loaded document declares the direction this project is auditing.
 *
 * Reports both `dir` and `lang`, because the two failures need different fixes: a
 * document that came back `lang="en"` means the locale never reached the negotiator,
 * and a document that came back `lang="ar" dir="ltr"` means `text_direction` and the
 * page shell have come apart.
 */
export async function assertRenderedDirection(
  page: Page,
  testInfo: TestInfo,
  label: string,
  path: string,
): Promise<void> {
  const want = expectedDirection(testInfo);
  const locale = testInfo.project.use.locale;
  expect(
    directionOfLocale(locale),
    `the ${testInfo.project.name} project audits ${want} and drives ` +
      `locale ${locale ?? "(none, so Playwright's device default)"}, which negotiates ` +
      `a ${directionOfLocale(locale)} document. Nothing downstream can recover a ` +
      `direction the browser was never asked for.`,
  ).toBe(want);
  const shell = await page.evaluate(() => ({
    dir: document.documentElement.getAttribute("dir"),
    lang: document.documentElement.getAttribute("lang"),
  }));
  expect(
    shell.dir,
    `${label} (${path}) rendered <html lang="${shell.lang}" dir="${shell.dir}"> where ` +
      `the ${testInfo.project.name} project audits ${want}. A pass here would be a ` +
      `pass for a direction this run never drew.`,
  ).toBe(want);
}
