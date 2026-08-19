# 0011. SEALED payloads are size-capped until at-rest encryption can stream

Status: Accepted

Date: 2026-08-19

Resolves: #141

## Context

Five separate places in this repository claimed that payloads are never held in RAM,
all of them invoking "a multi-gigabyte oral-history video" and "the one inexpensive box
the archive targets":

- `docs/ARCHITECTURE.md` — "verified without being held in RAM"
- `src/ledger/fixity.py` — "hashed without ever being held in RAM"
- `src/ledger/bag.py` — "the difference between bagging … and exhausting memory"
- `src/ledger/cas.py` — "arbitrarily large payloads cost constant memory"
- `src/ledger/server.py` — "must not cost gigabytes of RSS to serve"

Each is true of the code it annotates. Read together they assert an end-to-end property
that one path breaks, and it is the worst path for it to be: `ingest_sip` encrypts a
SEALED payload with `vault.encrypt_bytes(source.read_bytes())`, because Fernet has no
streaming API.

Measured, one file per size, peak RSS of the ingesting process:

| payload | PUBLIC (streamed) | SEALED |
| --- | --- | --- |
| 16.8 MB | 35.8 MB | 159.9 MB |
| 67.1 MB | 35.8 MB | 527.2 MB |
| 157.3 MB | 38.9 MB | **1189.3 MB** |

The streamed path is flat. The sealed path is linear at `peak_mb ≈ 33 + 7.4 × payload_mb`
— 31× the streamed path at 157 MB, and about 7 GB for a 1 GB oral history.

SEALED is what an at-risk contributor selects for the most sensitive material they have.
So the archive falls over precisely on the records it most needs to keep, and it does not
fall over with an error: it gets OOM-killed mid-ingest.

`docs/ideation/02-large-scale-fixes.md` (FIX-03) named this exact call site, fixed the
other two it named, and offered the alternative — "note the limitation and cap SEALED
payload size honestly until FIX-11's crypto review". Neither the cap nor the caveat was
written. Five unqualified claims shipped instead.

## Decision

**A SEALED payload larger than `Config.sealed_payload_max_bytes` (default 64 MiB) is
refused at ingest, and the five claims above are qualified to say so.**

The refusal happens in a pre-flight pass, before any payload is read, encrypted, or
stored — the same discipline as the existing duplicate-bag check, and for the same
reason: a precondition failure must leave the content store, temporary ciphertext, and
the identity vault untouched. Checking inside the payload loop would already have
written ciphertext for whichever files sorted first.

The message names the limit, the measured cost, the formula, and three ways forward
(store under a non-SEALED policy on an encrypted disk, split the file, or raise the
config value on a box with the memory).

**64 MiB is derived, then measured.** A 1 GB box is the smallest worth running this on;
`(512 − 33) / 7.4 ≈ 64 MB` is the payload whose peak fits half of it. The predicted peak
for 64 MiB was 506 MB and the **measured** peak is 527 MB — about half of 1 GB, which is
the number the docs now carry, because a predicted figure in a document about false
claims would be an embarrassing way to make the same mistake again.

### Why the cap rather than the real fix

The real fix is chunked framing for data at rest. That changes the on-disk encryption
format for the tier that protects a contributor's most sensitive material, and FIX-11
already records that the sealing layer's crypto **must not ship on self-review**.
Inventing an AEAD framing here — chunk boundaries, nonce derivation, truncation
resistance, rekeying — and shipping it unreviewed would be a far worse decision than
declining a large file. The cap is the honest interim the ideation document already
proposed; it is not the answer, and this ADR does not pretend it is.

### Why not simply document the limitation

Documenting alone leaves the OOM kill in place for anyone who does not read the
document. Failing closed with an actionable message is strictly better than a process
that dies, and it costs one size check.

## Consequences

- **An archive that today seals files over 64 MiB will start seeing refusals.** This is
  a behaviour change on the most sensitive tier, which is why the message explains the
  override rather than merely reporting a limit. Nothing rewrites existing bags; only
  new ingests are affected.
- **The five claims are now true as written.** Each names hashing, bagging, storing, or
  serving specifically, and each points at this ADR for the exception. The truthfulness
  gate (`tools/check_claims.py`) pins the caveat in all five files so it cannot be
  silently dropped back to an unqualified claim.
- **`Config` gains `sealed_payload_max_bytes`.** It defaults to 64 MiB, round-trips
  through the config file, and is validated as ≥ 1. Configs written before this load
  unchanged and take the default.
- **The measured multiplier is a published constant.** `SEALED_PEAK_RSS_MULTIPLIER = 7.4`
  lives in `config.py` and is quoted in the refusal, so an operator sizing their own cap
  works from the measurement rather than from a guess.
- **The limit is on the SEALED tier only.** SEALED payloads are never served on any read
  path — they are encrypted and never decrypted — so this bounds ingest, and no read
  path grows a new limit.
- **FIX-11 keeps the real fix.** When the commissioned crypto review lands, chunked
  at-rest framing should raise or remove the cap; this ADR is superseded at that point,
  not before.
