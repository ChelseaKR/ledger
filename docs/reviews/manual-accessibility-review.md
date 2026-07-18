# Manual accessibility review packet

## Purpose

This is a concise, reviewer-facing companion to the project’s full
[manual assistive-technology review cadence](../accessibility/MANUAL-REVIEW-CADENCE.md).
It is for a screen-reader and keyboard user to assess the actual locally served
synthetic archive. It does not replace paid accessibility expertise or constitute a
WCAG certification.

## Set up the synthetic review site

```sh
git clone https://github.com/ChelseaKR/ledger.git
cd ledger
make install
cd tools/a11y_browser
python -m serve_demo
```

Open the local address printed by the command (normally `http://127.0.0.1:8099`).
The site uses synthetic data only. Stop the local server with `Ctrl-C` when done.

## Suggested path

Use one assistive-technology/browser combination per pass. The project seeks both:

- NVDA with Firefox on Windows; and
- VoiceOver with Safari on macOS.

Exercise the canonical pages and states:

1. Browse page: landmarks, skip link, list/table equivalence, focus order.
2. Search: query label, result count and any status announcement.
3. Content-warning interstitial: warning announced before underlying content and
   clear “proceed” choice.
4. Record after proceeding: warning still available and reading order makes sense.
5. Contribution form: labels, instructions, validation and error announcement.
6. Steward console: keyboard-only controls and status feedback.

The full page list and detailed criteria are in the
[manual-review cadence](../accessibility/MANUAL-REVIEW-CADENCE.md).

## Report template

```text
Date:
Reviewer name or pseudonym (only if approved):
AT/browser and versions:
Keyboard-only path completed: yes/no
Pages and states completed:
Findings (include severity and synthetic reproduction):
What worked well:
May the maintainer publish this report or attribution: yes/no/edited only
```

Please do not include real personal, archive, or contributor data. The maintainer
will record agreed findings in the manual-review cadence and update the ACR where the
evidence changes the documented conformance position.
