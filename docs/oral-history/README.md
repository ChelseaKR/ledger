# Oral-history session kit (EXP-09)

Last verified: 2026-07-07 · Recheck cadence: per release

RM6 makes a transcript first-class *after the fact* — a steward types it in once a
recording already exists. This kit shapes consent *during capture* instead, which
is where an elder narrator (user research A3) actually negotiates what stays
public, what stays community-only, and what stays sealed for twenty years. It is
two things:

1. **A session manifest format** (JSON) a facilitator (or a later transcription
   pass) fills in with the segments marked during recording, each carrying its own
   disclosure policy and the moment the narrator's spoken consent for *that*
   policy was captured. See [`session-manifest-format.md`](session-manifest-format.md).
2. **A printable facilitator script** for running the consent conversation live,
   in the narrator's own words, using the archive's existing access-policy
   vocabulary. See [`facilitator-script.md`](facilitator-script.md).

No recording software is built or bundled here — the kit wraps whatever
recorder a community already has (a phone, a field recorder, a laptop). What it
adds is the *shape* that turns "we talked about it" into a manifest a steward can
review and the one ingest path (`ledger.ingest.ingest_sip`) can apply.

## The pipeline

```
   recording session                  after the session                  ingest
  ┌──────────────────┐   facilitator  ┌───────────────────┐   ledger    ┌─────────┐
  │ narrator speaks;  │ ───fills in──▶│ session-manifest.  │ ──session──▶│ Record  │
  │ facilitator marks │    segments   │ json (see format)  │  ingest CLI │ w/ per- │
  │ segment start/end │                │                    │             │ segment │
  │ + reads consent   │                │                    │             │ Fields  │
  │ script per segment│                │                    │             │ + audit │
  └──────────────────┘                └───────────────────┘             └─────────┘
```

`ledger.oralhistory.validate_session_manifest` refuses to let a manifest through
if any segment whose policy would ever disclose something to someone (`public`,
`community`, `stewards`, a date-bound `sealed-until`, or `sealed-conditional`)
has no recorded `spoken_consent_at`. That is the kit's one hard rule: **a
disclosed segment must trace to a moment the narrator agreed to it out loud.**
`ledger.oralhistory.apply_session_manifest` then maps a validated manifest onto a
`Record` — one descriptive `Field` per segment carrying the segment's own policy,
plus a `stewards`-only companion field recording when and how consent was given,
so the policy is provably traceable to the spoken moment on demand, without ever
publishing the consent conversation itself.

Run it with:

```sh
ledger session ingest --root <archive-root> --title "<session title>" \
  --manifest session-manifest.json \
  --file clip-01.wav=./clip-01.wav \
  --narrator-name "..." --narrator-contact "..."
```

`--narrator-name`/`--narrator-contact` are optional and, exactly like `ledger
ingest`, are sealed into the identity vault and never echoed back — only the
opaque `identity_ref` token is printed (no-outing rule). `--file` supplies the
actual bytes for any segment that named a `payload_filename` in the manifest
(e.g. a clipped audio segment); a segment with no `payload_filename` contributes
only its descriptive field.

## Consent-language review

The facilitator script's *wording* is a starting template. Per the portfolio's
standing rule for anything with legal weight (see the EXP-10 warrant-canary note
in `docs/ideation/03-expansions.md`), this kit does not claim a legal review that
has not happened: a community adopting it should have its own consent language
checked against local law and its own norms before relying on it, especially for
segments about to be sealed for a long embargo or released conditionally. What
*is* enforced in code, independent of the exact wording used, is the structural
rule above — a disclosed segment always carries a spoken-consent timestamp.
