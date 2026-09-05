# Automated accessibility coverage, by route

Last verified: 2026-09-05 · Recheck cadence: whenever a route is added, removed, or
gains coverage

This page names exactly which of ledger's HTML-emitting routes the two automated
accessibility engines reach today, and which do not have automated coverage from
either one. It exists so that gap is a stated, dated fact instead of a silent one
([#122](https://github.com/ChelseaKR/ledger/issues/122)) — the same principle
[`STATEMENT.md`](STATEMENT.md) applies to untested WCAG criteria, applied here to
untested *routes*.

This is a route inventory, not a conformance judgement. A route missing from both
engines is not known to be inaccessible — it is **not automatically checked**, which
is a different and narrower claim. See [`STATEMENT.md`](STATEMENT.md) for what "not
tested" means for the honest conformance picture.

## The two engines

- **The static gate** (`python -m ledger.accessibility_check web`, the `accessibility`
  CI job) scans `web/`'s static HTML (currently none — see below) plus a fixed set of
  server-rendered sample pages built by `_render_sample_pages()`.
- **The browser gate** (`axe.spec.ts` / `reflow.spec.ts`, the `accessibility-browser`
  CI job) drives a live `ledger serve` instance in headless Chromium for a fixed list
  of canonical pages.

Both lists are hand-maintained code, not derived from the route table, so a new route
does not automatically gain coverage — someone has to add it to one list or the other.
`tests/test_accessibility_route_coverage.py` keeps *this document* honest against that
code (see [Kept honest by a test](#kept-honest-by-a-test) below); it cannot make the
code itself grow coverage.

## Inventory

21 GET routes in `src/ledger/server.py` render HTML for a person rather than JSON, XML,
a binary payload, or a static asset. Counting `/record/{id}` once regardless of its two
content-warning states (as `axe.spec.ts` and `reflow.spec.ts` already do):

| Route | Static gate | Browser gate (axe/reflow) | Covered |
| --- | --- | --- | --- |
| `/` | Yes | Yes | Yes |
| `/search` | — | Yes | Yes |
| `/about` | — | Yes | Yes |
| `/how-it-works` | — | Yes | Yes |
| `/places` | Yes | — | Yes |
| `/timeline` | Yes | — | Yes |
| `/overview` | Yes | — | Yes |
| `/contribute` | Yes | Yes | Yes |
| `/withdraw` | Yes | — | Yes |
| `/edit` | Yes | — | Yes |
| `/transparency` | Yes | — | Yes |
| `/record/{id}` (both content-warning states) | Yes | Yes | Yes |
| `/steward` | — | Yes | Yes |
| `/status` | — | — | **No** |
| `/consent-status` | — | — | **No** |
| `/governance` | — | — | **No** |
| `/proof` | — | — | **No** |
| `/steward/audit` | — | — | **No** |
| `/record/{id}/consent` | — | — | **No** |
| `/record/{id}/object` | — | — | **No** |
| `/record/{id}/history` | — | — | **No** |

**13 of 21** routes have coverage from at least one engine. **8** have none.

Four of the covered routes — `/places`, `/timeline`, `/overview`, `/transparency` —
are covered by the static gate *only*. That is exactly the surface issue #122 was
about: before that fix, a `_render_sample_pages()` failure silently zeroed out the
static gate's coverage entirely, and these four routes' coverage would have
disappeared with it while CI stayed green. The static gate now refuses to report a
pass having examined zero documents, so that specific failure mode can no longer hide;
it does not add coverage to a route that was never covered. Two more —
`/withdraw` and `/edit` — are covered by the static gate only as of this fix (see
below).

## What this fix added: 3 routes, with no `server.py` change

Before this PR, the static gate rendered 6 sample pages (`/`, `/record/{id}`,
`/places`, `/timeline`, `/contribute`, `/transparency`) and the uncovered count was 11.
While diagnosing why closing that gap felt like it needed to touch `server.py`, three
of the eleven turned out not to: `_handle_overview`, `_handle_withdraw_form`, and
`_handle_edit_form` already build their `<main>` HTML by calling a **pure function**
`server.py` itself calls unmodified —

- `/overview` → `ledger.render._overview_main_html(records, lang=...)`, the same
  pattern `/places` and `/timeline` (already covered) use;
- `/withdraw` → `ledger.contribute.render_withdraw_main(lang=...)`, no arguments
  needed beyond the language;
- `/edit` → `ledger.contribute.render_edit_main(config, lang=...)`, needing only the
  `Config` `_render_sample_pages()` already builds.

`_render_sample_pages()` now calls all three, exactly as it already called
`_browse_main_html`, `_places_html`, `_timeline_html`, and
`contribute.render_contribute_main`. No line of `server.py` changed to add this
coverage — the routes were already built the right way; the static gate just was not
calling them yet. `/withdraw` and `/edit` were the two highest-priority gaps this issue
named — the pages a contributor uses to retract or tighten their own consent — so
closing them was worth doing in this PR rather than deferring alongside the rest.

## The remaining 8, and why this PR documents the gap instead of closing it

None of these 8 routes have automated accessibility coverage from either engine:

`/status`, `/consent-status`, `/governance`, `/proof`, `/steward/audit`,
`/record/{id}/consent`, `/record/{id}/object`, `/record/{id}/history`.

**`/record/{id}/consent` matters most** of the eight — it is the third of the three
highest-priority routes named in issue #122 (with `/withdraw` and `/edit`, both now
covered): the page a contributor uses to tighten or correct their own consent. Unlike
`/withdraw` and `/edit`, its handler (`_handle_consent_form`) builds its `<main>` HTML
inline rather than through an existing pure function, so reaching it means *adding*
one — a small, mechanical extraction (it depends on only `record_id` and one config
string), but a `server.py` edit nonetheless, deliberately left for separate,
reviewable follow-up rather than folded into the PR that fixes the zero-documents bug.
`/record/{id}/object` and `/status` are the same shape: simple inline HTML, an easy
extraction, still a `server.py` edit not made here.

`/record/{id}/history` and `/steward/audit` need more than an extraction: real
version-history/audit-log state (a record with prior versions, a populated PREMIS
event log) that the current one-record, never-edited sample archive does not have,
on top of being steward-gated views. `/consent-status` and `/proof` are inline HTML
depending on request-scoped state (a submitted consent request with a real reference
token; a published transparency attestation) that would need to be fabricated the same
way the sample archive already fabricates a record and a transparency entry —
plausible, but each is its own small design decision about what "sample" state to
assert, not a one-line addition. `/governance` shares its renderer
(`ServerHandler._info_page`) with `/about` and `/how-it-works`, both already covered by
the browser gate; extracting a pure version would benefit all three but, like the
others above, is a `server.py` change this PR does not make.

**Why draw the line at "no `server.py` change"?** `server.py` is this repo's largest
and most safety-sensitive module — its own comments call `do_GET` "the
disclosure/no-outing choke point" and describe it as deliberately *not* refactored
under audit time pressure. This PR's actual bug is in `accessibility_check.py`; adding
the three free routes stayed inside that same file. Extracting pure render functions
out of `server.py` handlers is real, worthwhile work — but it is a separable change
with its own review surface, and mixing it into the PR that fixes the zero-documents
detection bug would risk rushing exactly the handler code issue #122 is about
restoring trust in. Tracked as follow-up work; this document is what makes the
remaining gap a stated fact rather than a silent one in the meantime.

## A third source of coverage, for one criterion only (2026-09-05)

The table above is about the two general-purpose engines, and it stays that way. But
`tests/test_aria_live_status.py` (SC 4.1.3, status messages) drives a live server and
checks **`/steward` and `/consent-status`** for that one criterion — `/consent-status`
being one of the 8 routes with no engine coverage at all. That is a real, narrow
addition and it is recorded here rather than folded into the table, because writing it
into the "Covered" column would overstate it: those two routes are checked for status
messages and for nothing else. The 13/8 split above is unchanged.

## Kept honest by a test

`tests/test_accessibility_route_coverage.py` computes the same three-way split shown
above — the static gate's actual coverage (called live via `_render_sample_pages()`,
not hand-copied), the browser gate's hand-maintained list, and the route inventory —
and fails if the resulting "uncovered" set stops matching the 8 routes named here, or
if any of the route strings this document depends on disappear from
`src/ledger/server.py`'s dispatch table. If coverage changes, this file must change
with it, in the same PR, or that test fails.
