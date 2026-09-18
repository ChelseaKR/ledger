# Legal-process transparency (`/transparency`)

Traces to the portfolio's ideation notes for this repo, **EXP-10 — Warrant canary
and legal-process transparency page** (a Horizon-1 idea in the private planning
set this repo's remediation work draws from; not itself committed here, the way
`RESPONSIBLE-TECH-FRAMEWORK.md` is fetched at CI time rather than committed). Read
this document alongside [`docs/THREAT-MODEL.md`](THREAT-MODEL.md) §4.2 (Subpoena /
legal compulsion), which this feature is the community-facing counterpart to.

## What this is

A warrant canary in the established sense: a dated statement about legal demands
received, that a steward **re-attests to on a schedule**. The signal is not just
what the statement says — it is whether it keeps being refreshed. A steward who
goes silent (a gag order, a compromise, or simple neglect) and a steward with
nothing to report look identical for a while; the point of a canary is to make
that silence *visible* once the attestation goes stale, rather than letting a page
quietly go out of date while still reading as current.

`/transparency` renders the archive's most recent attestation from a durable,
hash-chained log (`ledger.transparency.TransparencyLog`). It is off by default
(`config.transparency_log_path` is empty) and, when on, never displays a stale
attestation as current, and never fabricates a statement where none exists.

## What this is *not* — the legal gate

**This feature ships mechanism only. It ships no legal wording.**

Per EXP-10's own risk note: *"canary wording and its legal effect vary by
jurisdiction — must not ship without counsel review."* A warrant canary's
legal force (if any), what it may safely say, and what happens the moment it
*cannot* be truthfully re-attested (a gag order attaching to a specific
demand) are questions only a lawyer, retained by the actual stewards of an
actual archive, in their actual jurisdiction, can answer. This repository is a
reference implementation with no real legal-demand history and no retained
counsel; it would be dishonest for it to ship canary text asserting a legal
posture it cannot back.

So, concretely:

- Every `Attestation` carries a `counsel_reviewed` flag. `/transparency` shows an
  explicit, unmissable warning on any attestation where this is `false`: **"This
  statement has not been reviewed by counsel."**, followed by *"Its wording is a
  placeholder and carries no asserted legal effect."* Two sentences rather than one,
  so the emphasis does not have to live inside a translatable string.
- `ledger transparency attest` requires `--statement` explicitly (no built-in
  default text a steward could accidentally ship unreviewed) and prints the same
  warning to stderr whenever `--counsel-reviewed` is not passed.
- Before a real archive relies on this page, its stewards should retain counsel to
  review: the specific wording proposed, what a `national_security_letter` or
  sealed-order nondisclosure requirement would legally permit the statement to say
  (in some jurisdictions, even confirming "we cannot confirm or deny" may be
  restricted), and what a steward must do if a demand arrives that prevents
  further truthful re-attestation. Once reviewed, attest with
  `--counsel-reviewed --counsel-note "<date, reviewer, scope>"`.

### The statement is never translated

`/transparency` is served in English, Spanish, French and Arabic. **The statement
itself is not.** It is reproduced byte for byte as the stewards published it, in the
language they wrote it in, and so is the counsel note beside it.

That is the same reasoning as the section above, one step further on. The wording is
the instrument; a translation of it is a *different* wording, and this project — which
has no retained counsel and did not write the statement — cannot say the two assert
the same thing. A canary that says slightly less in Arabic than it says in English is
not a localization bug, it is a false statement about legal process to the reader
least able to check it.

So the page tells the reader what they are looking at, in their own language, in the
sentence immediately above the statement: that it is reproduced exactly as published,
in the language it was written in, because its wording is a legal instrument — and
that everything else on the page is this project's own explanation and *is*
translated. The same applies to the demand-type vocabulary
(`transparency.DEMAND_TYPES`): `national_security_letter` names one specific US
instrument, and a target-language word for it would assert an equivalence across
jurisdictions that may not exist, so the terms stay in the legal system's own words
with a translated sentence saying why.

A steward who serves a non-English readership should write the statement in the
language that readership reads, with counsel who practices there — the page will
reproduce whatever they publish. `tests/test_i18n_rtl.py` holds this line from the
other side: the statement text and the demand-type keys are the only things allowed to
survive un-seamed in that page's body, and each is named there with this reason.

## Using it

```console
# One-time: point an archive's config at a log file.
$ python -c "
import json
from pathlib import Path
p = Path('archive-root/store/config.json')
data = json.loads(p.read_text())
data['transparency_log_path'] = 'archive-root/transparency.json'
data['transparency_cadence_days'] = 90  # how often a steward must re-attest
p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
"

# Re-attest (run on the cadence you configured, e.g. from cron/CI):
$ ledger transparency attest --root archive-root \
    --by steward-handle \
    --statement "As of this date, N legal demands of type X have been received." \
    --count subpoena=0 --count court_order=0 \
    --counsel-reviewed --counsel-note "Reviewed 2026-06-01 by outside counsel."

# Inspect the latest attestation and verify the hash chain:
$ ledger transparency show --root archive-root
```

## How it is verifiable

Each attestation is chained to the one before it by a SHA-256 digest over its own
content plus the previous entry's digest (`Attestation.content_digest`,
`TransparencyLog.append`) — the same tamper-evidence discipline as ledger's PREMIS
event log. `ledger transparency show` (and the page itself) recompute the chain
from the log file alone (`ledger.transparency.verify_chain`); editing, reordering,
or deleting a past entry breaks it, and a third party with only the log file — not
trusting the steward — can detect that.

This is **tamper-evidence, not a cryptographic signature by a named individual.**
A steward may additionally paste an out-of-band signature (for example, the output
of `ssh-keygen -Y sign -f <key> -n transparency <statement-file>`) into the
`--signature` field; `TransparencyLog` stores it opaquely alongside the
attestation but does not verify it — a reader who wants that stronger guarantee
verifies it themselves with `ssh-keygen -Y verify`, against a key the steward has
published or shared out of band. A fully automated, publicly verifiable signing
mechanism (age/ssh-keygen-backed, checked by the software itself) is shared,
not-yet-built work with EXP-01 (public transparency attestations on `/proof`).

## What a visitor should take from a stale or absent page

- **Feature not configured** (`transparency_log_path` empty): the archive makes no
  claim, positive or negative. Absence of the feature is not evidence of anything
  — most archives running today's ledger have not opted in yet.
- **Configured but never attested**: the steward has opted in but not yet
  published a first statement.
- **Attested but stale** (older than `transparency_cadence_days`): shown with an
  explicit warning. Treat the statement as not current. A stale canary is the
  visible signal this whole mechanism exists to produce — investigate, don't
  assume the best or the worst.
- **Attested and current, but not counsel-reviewed**: the wording is a
  placeholder with no asserted legal effect, clearly labeled as such.
