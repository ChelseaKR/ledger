# Accessibility statement

**Last verified: 2026-08-01 · Recheck cadence: per release and quarterly**

This is ledger's public accessibility statement (A11Y-16). It says what we have
tested, what we have **not** tested, and what to do if the software fails you.

For the criterion-by-criterion detail, read the
[Accessibility Conformance Report](./ACR.md). This page is the plain-language
summary, and it is deliberately shorter than the ACR.

## What this covers

ledger is a **reference implementation**, not a hosted service. This statement
covers the public browse/search surface that `ledger serve` renders — the pages a
community's readers see — and the steward console behind an authenticated grant.

It does **not** cover any particular community's deployment. If you are reading a
real archive, its operators may have changed the templates or the stylesheet, and
only they can speak for what they shipped.

## Conformance status

**Partially conformant with WCAG 2.2 Level AA.** "Partially" is doing real work in
that sentence, and the reason is in [What we have not tested](#what-we-have-not-tested).

The ACR records 46 criteria as Supports and 6 as Partially Supports, with none as
Does Not Support. We publish the partial rows rather than rounding them up.

## What we have tested

Every check below runs on **every commit** and blocks the merge if it fails. None
of them are run by hand or on request, because a check that is run by hand is a
check that stops being run.

| Check | What it covers | Where |
|---|---|---|
| Structural gate | `lang`, `<title>`, single `<h1>`, `<main>`, skip link, `alt`, `<label for>`, table `<caption>`/`<th scope>`, no positive `tabindex` | `python -m ledger.accessibility_check web` (`accessibility` job) |
| axe-core, real browser | WCAG-tagged violations on every canonical page, under **both** light and dark colour schemes | `axe.spec.ts` (`accessibility-browser` job) |
| Keyboard traversal | The contribute form reachable and operable with the keyboard alone | `keyboard.spec.ts` (same job) |
| Reflow at 320 CSS px | SC 1.4.10 — no horizontal page scroll at 320×256, which is a 1280px screen at 400% zoom | `reflow.spec.ts` (same job) |

The reflow check is newer than the others and it found two real defects on its
first run: a record permalink and a citation string were unbreakable tokens wider
than the viewport, so a single URL made the whole page scroll sideways and every
line of the record had to be read by panning. Both are fixed. We mention this
because the ACR had claimed 1.4.10 as "Supports" before anything tested it — which
is exactly the failure mode a written statement is supposed to prevent.

## What we have not tested

**No screen-reader review has been performed yet.** This is the honest gap and it
is the reason for "partially conformant" above.

Automated checks — including all four in the table above — prove the *structural*
floor. They cannot judge whether the reading order makes sense, whether the
content-warning interstitial is *announced* before the material is reached,
whether an `aria-live` status is actually *heard*, or whether a form error is
understandable when it is spoken. Those are judgements only a person using the
technology can make.

[`MANUAL-REVIEW-CADENCE.md`](./MANUAL-REVIEW-CADENCE.md) commits us to a quarterly
NVDA + Firefox and VoiceOver + Safari pass and holds the results log. That log is
currently empty. We will not mark it complete from an automated scan, and we ask
you not to read the green CI badge as though it were a screen-reader pass.

Tracked at [#81](https://github.com/ChelseaKR/ledger/issues/81).

Also untested, and named rather than omitted:

- **Magnification and text-spacing** beyond the 320px reflow case (SC 1.4.4, 1.4.12).
- **Speech input** (Dragon, Voice Control).
- **Cognitive-load review** by anyone other than the maintainer.

## Known limitations

- The record table scrolls horizontally inside its own box on a narrow screen.
  This is permitted for tabular data under SC 1.4.10, but it is still a worse
  experience than the equivalent list view — which is always available and carries
  the same information.
- Content warnings are enforced with a server-rendered interstitial, so proceeding
  past one costs a page load. That is deliberate: the warning must exist before
  the material is rendered, not be hidden with CSS.

## Feedback

If any part of ledger is not usable for you, that is a defect and we want the
report. Please open an issue at
<https://github.com/ChelseaKR/ledger/issues>, or use the private reporting path in
[`SECURITY.md`](../../SECURITY.md) if the report would expose something you would
rather not post publicly.

Please say what you were trying to do, what happened, and which assistive
technology, browser, and operating system you were using — the last three are what
make a report reproducible. You do not need to know which WCAG criterion it is.

We aim to acknowledge accessibility reports within five working days. This is a
volunteer-maintained project and that is a target, not a contractual commitment;
saying so is more useful than promising a number we cannot always hit.

## If you deploy ledger

This statement describes the reference implementation. If you run ledger for a
community, **publish your own statement**: your deployment, your templates, your
contact address, and your own manual-review record. Copying this page without
doing the review would attach our evidence to your software, which is not the same
software.
