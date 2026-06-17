# ledger — web (the accessible public face)

This directory holds the framework-free browse/search site: the only ledger
surface most people ever touch. It is rendered in plain Python by
`src/ledger/server.py` over the standard-library `http.server` (no web framework,
no build step, no JavaScript) and styled by one stylesheet, `static/app.css`.

Two qualities dominate every decision here:

- **Accessibility** — the site targets **WCAG 2.2 AA**.
- **Safety (the no-outing rule)** — the site can never render a contributor
  identity. Every record reaches a response only through `Archive.browse` /
  `Archive.disclose`, i.e. `ledger.access.disclose`, whose output type
  (`DisclosedRecord`) structurally has no `identity_ref`. No route, query
  parameter, header, JSON field, log line, health summary, or error page exposes
  identity. The access log is overridden to record method + status + the *path
  only* (query string stripped), so a grant subject or a search term is never
  logged.

## Layout

```
web/
  README.md          this file
  static/
    app.css          the one stylesheet (skip-link styles are folded in here)
```

HTML is generated in Python, so there is no `templates/` directory. The skip-link
styles live in `app.css` rather than a separate `skip-link.css`.

## Running it

```python
from pathlib import Path
from ledger.config import Config
from ledger.ingest import Archive
from ledger.server import serve

archive = Archive(Config.load(Path("store/config.json")))
serve(archive, host="127.0.0.1", port=8000, grants_path=Path("grants.json"))
```

The server binds to `127.0.0.1` by default (not `0.0.0.0`): a fresh archive is
reachable only from the local box until an operator deliberately puts it behind a
vetted reverse proxy (securability — do not bind the world by default).

## Routes

| Route                | Returns                                                        |
| -------------------- | ------------------------------------------------------------- |
| `GET /`              | Accessible browse page: a **list view and a table view** of every record the viewer may list. |
| `GET /record/{id}`   | A single record. If it carries content warnings, a **text interstitial** is shown before the content. |
| `GET /search?q=`     | The browse page filtered over **disclosed** titles and descriptions. |
| `GET /api/records`   | JSON array of `DisclosedRecord.to_dict()` for listable records. |
| `GET /api/record/{id}` | JSON of one `DisclosedRecord.to_dict()` (same disclosure gate). |
| `GET /healthz`       | JSON health plus a **fixity-status summary** (counts only, scrubbed of identity). |
| `GET /static/...`    | Files under `web/static`, with path-traversal blocked.        |

`HEAD` is supported on every route (headers only, no body).

### Grant resolution (deny by default)

Requests are **anonymous** unless an `X-Ledger-Grant: <subject>` header names a
subject that exists in the grants file passed as `grants_path`. An unknown or
absent subject falls back to anonymous; the header is never trusted beyond looking
up a pre-provisioned grant (least privilege). Anonymous viewers see only `PUBLIC`,
unsealed material.

## Accessibility (WCAG 2.2 AA) — how each requirement is met

**Document structure**

- Every page starts with `<!doctype html>` and `<html lang="…">` (the archive's
  configured primary language).
- Each page has a unique, descriptive `<title>` (e.g. the record's title).
- Landmarks: `<header>`, `<nav aria-label="Site">`, `<main id="main">`,
  `<footer>`. There is exactly **one `<h1>` per page**, and headings descend
  without skipping levels (`h1` page title → `h2` section → `h3` list item).

**Bypass blocks / keyboard**

- The **first focusable element** is a visible "Skip to main content" link
  (`.skip-link`) that targets `#main`. It is off-screen until focused, then
  appears top-left with a high-contrast background (WCAG 2.4.1).
- `<main>` has `tabindex="-1"` purely as the skip-link target; it shows no focus
  ring when focused programmatically.
- **No positive `tabindex`** is used anywhere, so focus order follows source
  order (WCAG 2.4.3).
- A strong, consistent `:focus-visible` outline (3px, offset, `--accent`) marks
  the focused control; it clears the 3:1 non-text contrast minimum (WCAG 2.4.7,
  2.4.11 Focus Not Obscured).
- Interactive controls (search input, buttons, links) have a minimum 44px tap
  target (WCAG 2.5.8 Target Size).

**Forms**

- The search field has a real `<label for="q">` tied to the input's `id`, so its
  purpose is announced. The form is wrapped in `role="search"`.

**Colour & signals**

- Colour is **never the only signal**. A record with content warnings shows the
  literal word "Content warning" as a text badge in the list, a "Yes/No" text
  column in the table, and a full text interstitial on the record page.
- Documented contrast tokens (all AA-passing against white) are listed at the top
  of `app.css`; e.g. body text 16.1:1, links 6.5:1, content-warning text 8.2:1.

**Images & motion**

- The site ships **no images**; nothing depends on an image to convey meaning, so
  there is no missing `alt` text. Were a decorative image ever added it would use
  `alt=""`; an informative one would carry descriptive `alt`.
- `@media (prefers-reduced-motion: reduce)` disables transitions, animations, and
  smooth scrolling for users who request reduced motion (vestibular safety).

**Links**

- Link text is always descriptive (a record's title, "Proceed to the content",
  "Back to all records") — never "click here" (WCAG 2.4.4).

**Responsive / mobile-first**

- The stylesheet is mobile-first and fluid; `max-width: 70ch` keeps a readable
  line length, the table scrolls horizontally on narrow screens instead of
  overflowing, and the layout reflows to a single column on small screens (WCAG
  1.4.10 Reflow). It works on an inexpensive phone (mobility/ubiquity).

## The list/table equivalent (documented non-visual equivalent)

The browse and search pages present the **same records twice**: once as a
semantic `<ul>` list (`Records (list view)`) and once as a data `<table>`
(`Records (table view)`). The two are content-equivalent, so a user of a screen
reader, a magnifier, a narrow screen, or a text browser gets the complete
information in whichever form suits them.

The table is fully marked up for assistive technology:

- a `<caption>` states the table's purpose;
- every header cell uses `<th scope="col">`, associating each data cell with its
  column;
- the "Content warning" column carries the literal text "Yes"/"No" (no
  colour/icon dependency).

## The content-warning interstitial

On `GET /record/{id}`, if the record carries any content warnings, the page first
shows **only the title and a textual interstitial**: a region headed "Content
warnings", the warnings listed as words, and a link "Proceed to the content"
(`?proceed=1`). The signal is therefore **programmatic and textual**, not
conveyed by colour or an icon alone (the warning border/background only reinforce
the words). The underlying content is not rendered until the viewer chooses to
proceed; even after proceeding, the warnings are restated as text above the
content so the signal is never lost.

## Honesty about redactions

A record may disclose only part of itself under a given grant. Any withheld field
or payload is named in a plain-text "Withheld" section, so the partial view is
honest about being partial (honesty, fidelity) — and, of course, the withheld
*values* are never sent.

## Security notes

- Every interpolated string passes through `html.escape` (the module's single
  text-to-HTML boundary), so untrusted text cannot break page structure or inject
  script (no XSS).
- Responses carry a strict `Content-Security-Policy` (`default-src 'none'`,
  styles/images `'self'`, no scripts), `X-Content-Type-Options: nosniff`, and
  `Referrer-Policy: no-referrer`.
- `GET /static/...` resolves every request under the canonical `web/static` root
  and refuses anything that escapes it (no path traversal). Unknown file suffixes
  are served as `application/octet-stream`, never as active content.
- A "not found" and a "not permitted to list" render the **same** neutral page,
  so a response never reveals whether a sealed record exists (confidentiality).
