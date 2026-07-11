# Session manifest format (EXP-09)

Last verified: 2026-07-07 · Recheck cadence: per release

A session manifest is a single JSON document a facilitator (or a later
transcription pass) produces from a recording session. It is parsed and applied
by `ledger.oralhistory` (`parse_session_manifest`, `validate_session_manifest`,
`apply_session_manifest`) and consumed by the `ledger session ingest` CLI
subcommand. Reference implementation: `src/ledger/oralhistory.py`.

## Top level

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `session_id` | string | yes | A facilitator-chosen identifier for the recording session (not a `ledger` record id — a fresh record id is minted at ingest). |
| `recorded_at` | string (ISO-8601) | yes | When the session took place. |
| `facilitator` | string | yes | A **role**, not a person's name (e.g. `"community steward"`). Never treated as identity, but also never asserted to *be* identity-free by this format alone — write a role, not a name. |
| `narrator_ref` | string or `null` | no | An opaque, session-scoped label (e.g. a claim token, or simply `"narrator"` for a single-narrator session) — **never a name or contact**. A narrator's real identity, if sealed at all, goes through the same `--narrator-name`/`--narrator-contact` sealing every other ledger ingest uses, never through this field. |
| `segments` | array of segment objects | yes, non-empty | The consented segments captured during the session, in any order (segment markers, not manifest order, determine playback order). |

## Segment object

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `segment_id` | string | yes, unique within the manifest | A short, stable id for the segment (e.g. `"seg-01"`). |
| `label` | string | no | A short, disclosable description of the segment (becomes the segment's `Field` value if no `transcript` is given). |
| `start_seconds` / `end_seconds` | number | yes | Segment markers as offsets into whatever single recording the community's own recorder produced. `end_seconds >= start_seconds >= 0`. |
| `policy` | string | no (default `"sealed-until"`) | One of ledger's `AccessPolicy` values: `public`, `community`, `stewards`, `sealed-until`, `sealed-conditional`, `sealed`. |
| `unseal_at` | string (ISO-8601) or `null` | required if `policy` is a date-bound `sealed-until` | The date this segment opens up. Omit (or leave `null`) for `sealed-until` to mean "sealed indefinitely." |
| `unseal_condition` | string or `null` | required if `policy` is `sealed-conditional` | The named condition a steward checks before unsealing (see `docs/GOVERNANCE.md`). |
| `spoken_consent_at` | string (ISO-8601) or `null` | **required whenever the policy ever discloses to anyone** | The moment, within the session, the narrator's spoken agreement to *this segment's policy* was captured. Not required for `sealed` (absolute) or indefinitely-sealed `sealed-until` segments, since those never disclose to anyone — though recording one anyway is good practice. |
| `consent_note` | string | no | The facilitator's brief paraphrase of what was agreed (e.g. `"narrator: public, this is the part I want people to hear"`). Lands only in a `stewards`-only audit field — never published. |
| `payload_filename` | string or `null` | no | If this segment is also a distinct audio/video file (e.g. a clipped excerpt), the filename supplied at ingest via `--file filename=path`. The file inherits the segment's own `policy`. |
| `transcript` | string | no | Plain-text caption/transcript for the segment, carried the same way `ledger ingest --transcript` carries one (WCAG 1.2, user research H3). |

## The one hard rule

`validate_session_manifest` raises if a segment's policy will *ever* disclose the
segment to someone — `public`, `community`, `stewards`, a date-bound
`sealed-until`, or `sealed-conditional` — but `spoken_consent_at` is empty. It
also raises if a `sealed-conditional` segment names no `unseal_condition`, or a
date-bound `sealed-until` segment's `unseal_at` is blank. A manifest that fails
validation is never applied to a record (fail closed) — nothing is scaffolded, so
a session with an unresolved consent gap cannot accidentally ingest with the gap
left silent.

## Example

```json
{
  "session_id": "2026-07-elders-circle-03",
  "recorded_at": "2026-07-02T18:00:00Z",
  "facilitator": "community steward",
  "narrator_ref": "narrator",
  "segments": [
    {
      "segment_id": "seg-01",
      "label": "how the mutual-aid fridge started",
      "start_seconds": 0,
      "end_seconds": 240,
      "policy": "public",
      "spoken_consent_at": "2026-07-02T18:03:10Z",
      "consent_note": "narrator: public, this is the part I want people to hear",
      "payload_filename": "seg-01.wav"
    },
    {
      "segment_id": "seg-02",
      "label": "names of people still living who were involved",
      "start_seconds": 240,
      "end_seconds": 410,
      "policy": "sealed-until",
      "unseal_at": "2046-07-02T00:00:00Z",
      "spoken_consent_at": "2026-07-02T18:07:45Z",
      "consent_note": "narrator: seal this part twenty years, folks are still around",
      "payload_filename": "seg-02.wav"
    },
    {
      "segment_id": "seg-03",
      "label": "a detail the narrator asked never be shared",
      "start_seconds": 410,
      "end_seconds": 480,
      "policy": "sealed"
    }
  ]
}
```

`seg-01` is public and has a spoken-consent timestamp. `seg-02` is sealed for
twenty years and also has one, plus the date it opens. `seg-03` is sealed
absolutely — it never discloses to anyone, including stewards, so no
`spoken_consent_at` is required (though the facilitator could still record the
moment the narrator asked for this).
