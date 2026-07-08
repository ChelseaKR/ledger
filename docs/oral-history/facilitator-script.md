# Facilitator script — oral-history recording session (EXP-09)

Last verified: 2026-07-07 · Recheck cadence: per release

**Print this page.** It is meant to sit next to the recorder, not to be read from
a screen. It walks a facilitator through starting a session, marking segments,
and capturing spoken consent *for each segment* in the narrator's own words — the
raw material that later becomes a [session manifest](session-manifest-format.md).

> This script's wording is a starting template, not vetted legal language. A
> community should review and adapt it — content, phrasing, and any local-law
> considerations — before relying on it for a real session. What the archive
> enforces in code, independent of the exact words used, is only the structural
> rule: **a segment that will ever be shown to anyone must have a spoken-consent
> moment recorded for it.**

## Before recording

- [ ] Confirm who is present and get everyone's spoken OK to be recorded at all.
- [ ] Explain, in plain language, the disclosure levels this archive uses, so the
      narrator is choosing between real options, not guessing:
  - **public** — anyone who visits the archive can see this.
  - **community** — only vetted members of this community can see this.
  - **stewards** — only the people who run the archive can see this (for care and
    context, not publication).
  - **sealed for a time** — hidden until a date the narrator picks (a year, ten
    years, whenever people involved are more likely to have passed).
  - **sealed until a condition** — hidden until something specific happens (a
    steward will check for and record when that condition is met — see
    `docs/GOVERNANCE.md`).
  - **sealed completely** — hidden from *everyone*, including the people who run
    the archive. Nothing short of the narrator asking for it to open ever
    unseals this.
- [ ] Agree on segment breaks loosely in advance ("let's talk about the fridge,
      then about names, then about the thing you don't want shared") — segments
      do not have to be planned perfectly; they can be marked as the
      conversation actually goes.
- [ ] Have a clock or timestamp source visible so segment start/end times and
      consent moments can be marked accurately.

## During recording — per segment

For **each** segment, once the narrator has said what they want to say:

1. **Mark the segment boundaries.** Note the start and end time (seconds from
   the start of the recording, or clock time you'll convert later).
2. **Ask, out loud, on the recording:** *"For what you just told me about
   [topic] — who should be able to hear this, and when?"* Let the narrator
   answer in their own words; you do not need to make them use the archive's
   exact vocabulary, but you (the facilitator) map their answer onto one of the
   disclosure levels above once they've said it.
3. **Reflect it back, on the recording:** *"So to confirm — [topic] will be
   [public / community-only / sealed until 20XX / sealed until (condition) /
   sealed completely]. Is that right?"* Get an audible yes.
4. **Note the moment.** Write down (or have your recorder auto-timestamp) the
   instant this exchange happened — this becomes `spoken_consent_at` for the
   segment. This is the single most important thing this script produces: a
   provable link between a policy and a moment the narrator agreed to it.
5. **Note anything the narrator adds** ("only after my sister's seen it first",
   "this part's for the archive, not the newsletter") as a short paraphrase —
   this becomes the segment's `consent_note`. It is never published; it lives
   only where stewards can see it, as the record of *why* the policy is what it
   is.

## After recording

- [ ] Fill in a [session manifest](session-manifest-format.md) JSON file: one
      segment entry per marked segment, with its `policy`,
      `spoken_consent_at`, and `consent_note` from your notes.
- [ ] Give the narrator a copy of what you wrote down for each segment before
      you ingest anything — they should recognise every policy you're about to
      apply.
- [ ] A steward reviews the manifest and runs `ledger session ingest` to apply
      it. Ledger refuses to ingest any segment marked to ever be shown to
      anyone that has no `spoken_consent_at` recorded — so a gap here surfaces
      as a hard error, not a silent guess.
- [ ] If the narrator changes their mind about a segment's disclosure *after*
      ingest, that is an ordinary consent change — the same `docs/GOVERNANCE.md`
      process (and `ledger policy` / `ledger seal`) that governs every other
      record and field, since the segment is, at that point, just a field like
      any other.
