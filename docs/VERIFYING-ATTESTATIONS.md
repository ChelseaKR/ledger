# Verifying a transparency attestation (EXP-01)

`/proof` on any ledger site links to `/proof/attestation.json`: a small, dated
document a steward publishes periodically (via `ledger attest-health`, typically
from a cron job) so that a contributor, a partner, or a rival fork can check the
archive's health **without trusting the server they got it from**. This document
explains the format and how to check it yourself.

## What is published, and what is deliberately left out

```json
{
  "schema_version": 1,
  "archive_name": "Example Archive",
  "generated_at": "2026-07-07T09:00:00Z",
  "software_version": "0.1.0",
  "fixity_ok": true,
  "chain_head_summary": "b1946ac92492d2347c6235b4d2611184...",
  "signature": {
    "format": "ssh",
    "value": "-----BEGIN SSH SIGNATURE-----\n...\n-----END SSH SIGNATURE-----\n"
  }
}
```

* **`fixity_ok`** — whether every stored bag passed its most recent checksum
  audit. A `true` here is only ever as recent as `generated_at`; it is not a
  live guarantee about this exact instant.
* **`chain_head_summary`** — one SHA-256 hash committing to the *entire history*
  of every append-only PREMIS log in the archive (every record's event log, plus
  the archive-level takedown and key-rotation logs). Editing, removing, or
  reordering a single past event anywhere changes this value.
* **`software_version`** — the `ledger` release the steward ran when publishing.

Deliberately **not** published: a bag count, a per-record breakdown, or a list of
individual log heads. ledger keeps absolute counts steward-only everywhere else
in the codebase (see the `P2-2` references in `src/ledger/server.py` and
`docs/THREAT-MODEL.md`) — a public counter that ticks up over time would let an
outside observer infer *when* a record, possibly a sealed one, was added, and
correlate that against a contributor's real-world timeline. `chain_head_summary`
gives the same tamper-evidence (a rewrite anywhere changes it) without that leak.

## Checking tamper-evidence: compare two dated attestations

Because `chain_head_summary` commits to full history, not just the latest
change, an archive that quietly rewrote or rolled back its history cannot make
two attestations taken at different times agree with a *consistent* forward
history unless the summary actually changed between them in a way that matches
what was added. Concretely:

1. Save the `.json` file each time you fetch `/proof/attestation.json` (or keep
   ledger's own `attestations/` directory, which never deletes an old one).
2. If a later `generated_at` claims *more* activity happened but
   `chain_head_summary` is **identical** to an earlier attestation, either
   nothing was actually recorded (fine), or an operator swapped in a stale
   attestation file by hand (not fine — ask why).
3. If `chain_head_summary` ever reverts to a value it held at an *earlier* date
   after having changed away from it, some log was rolled back to a prior state
   — a rewrite, by definition, since normal operation only appends.

This does not tell you *which* record or log changed (by design — see above);
it tells you whether the archive's claimed history is internally consistent
over time. Combined with independent replicas cross-checking their own chain
heads (`ledger replicas`), this is what lets ledger's threat model claim
tamper-evidence against an operator with raw disk access, not just against a
network attacker.

## Checking the signature

If `signature` is present, it was produced with
[`ssh-keygen -Y sign`](https://man.openbsd.org/ssh-keygen#Y) using a key the
steward controls — no bespoke cryptography, no new runtime dependency. To
verify:

1. Get the steward's SSH **public** key out of band (their website, a prior
   in-person exchange, a keybase-style proof — however you'd verify any SSH key).
   Put it in an "allowed signers" file, e.g. `allowed_signers`:

   ```
   steward@example.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...
   ```

2. Fetch the attestation and re-derive the exact bytes that were signed —
   every field *except* `signature` itself, as compact canonical JSON (sorted
   keys, no extra whitespace):

   ```bash
   python3 -c "
   import json, sys
   d = json.load(open('attestation.json'))
   d.pop('signature', None)
   sys.stdout.write(json.dumps(d, sort_keys=True, separators=(',', ':')))
   " > attestation.signed-payload

   python3 -c "
   import json
   json.load(open('attestation.json'))['signature']['value']
   " # or just copy the 'value' field's text into attestation.sig
   ```

3. Verify:

   ```bash
   ssh-keygen -Y verify \
     -f allowed_signers \
     -I steward@example.org \
     -n ledger-health-attestation \
     -s attestation.sig \
     < attestation.signed-payload
   ```

   `ssh-keygen` prints `Good "ledger-health-attestation" signature` on success.
   The `-n ledger-health-attestation` namespace must match exactly — it stops an
   attestation signature from being replayed as a signature over anything else
   the same key signs (a git commit, an SSH login, another archive's
   attestation).

If there is no `signature` field, the archive has not configured a signing key
yet (`attestation_signing_key` in its config) — `fixity_ok` and
`chain_head_summary` are still meaningful, but nothing here proves who
published them.

## Publishing attestations (for a steward)

```bash
ledger attest-health --root /path/to/archive --signing-key ~/.ssh/ledger_attest_ed25519
```

Run this on a schedule (cron, systemd timer, CI). It re-audits fixity (the same
work `ledger audit` does), computes the chain-head summary, signs the result if
a key is configured, and writes it under the archive's `store/attestations/`
directory, where `/proof` and `/proof/attestation.json` serve it from. The
`--signing-key` flag overrides the `attestation_signing_key` config field; a
run with no key configured either way still publishes, just unsigned, so a
fresh archive is never blocked on key setup. The command exits non-zero when
the fixity audit found a problem, so a cron failure alerts a steward the same
way `ledger audit` does.
