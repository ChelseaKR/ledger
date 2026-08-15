# Mutual preservation aid: encrypted replica exchange (EXP-15)

Community archives run on volunteer labor and borrowed infrastructure. A raided
office, a burned-out collective, or a single steward's hard drive dying can take
years of documentation with it. ledger already lets one archive keep several of its
own copies and rebuild a failing one (`ledger replicas`, `ledger heal`); this document
covers the second
transport in that module — one archive holding an **encrypted** copy of a sibling
archive's bags, and vice versa, so a raided or burned-out instance can recover from
a partner it never had to fully trust with its contents.

This is the implementation described in `docs/ideation/03-expansions.md` (EXP-15).
It closes the residual named in the threat model: a replica host that is hostile,
compromised, or subpoenaed can read whatever it holds *if* it holds plaintext.
Under this transport it never does.

## The shape of the exchange

* **Key stays home.** Each pairing uses a symmetric key (Fernet, via the
  `cryptography` package already used by the identity vault) that the *owning*
  instance generates and keeps. It is never written into the sealed blob, never
  sent to the partner, and this codebase never logs it.
* **The partner holds ciphertext, not a bag.** `ledger mutual-aid seal` tars the bag,
  encrypts it, and writes a single `<bag-name>.sealed` file to the partner's storage.
  The partner's copy of `ledger` never runs `validate_bag` against it — it cannot,
  since it does not have the key — and it never needs to.
* **Fixity is exchanged as a digest, on a schedule.** `ledger mutual-aid attest`
  computes the SHA-256 of whatever ciphertext bytes are currently on disk. It needs
  neither the key nor an archive, so the *holding* partner can run it on a cron job
  over a directory of blobs they cannot read (mirroring how `ledger attest-health`
  already works for EXP-01) and report the digest back out-of-band. The owner checks
  it against the digest `seal` printed with `ledger mutual-aid verify`, which exits
  non-zero on a mismatch. A mismatch means the partner's copy drifted, was
  substituted, or went missing — evidence to act on well before an actual loss forces
  the question.
* **Recovery is a drill, not an assumption.** `ledger mutual-aid recover` pulls the
  ciphertext back, decrypts it locally with the key that never left home, and runs it
  through the same `validate_bag` used everywhere else in the archive, exiting
  non-zero if the recovered bag does not validate. Run this periodically against a
  live partner pairing — the "Excellent" bar from the ideation pitch is a full
  recovery drill on commodity hardware, with the partner provably unable to read
  anything it hosted.

The underlying functions (`seal_bag`, `replicate_sealed_bag`, `attest_sealed_replica`,
`verify_sealed_attestation`, `recover_sealed_bag`) stay available in
`ledger.replicate` for anyone scripting against the library, but nothing below
requires writing Python.

## Setting up a pairing

1. Register the partner as a `mirror` `StorageLocation` the way any replica target
   is registered (`ledger add-location`), pointing at wherever the partner exposes
   storage to you (a synced directory, an SFTP mount, object storage — anything
   `Path` can address once mounted).
2. Generate a pairing key out-of-band, once, with a CSPRNG:
   ```sh
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Exchange it with the partner over a channel you already trust for sensitive
   coordination (the same channel you would use to coordinate a takedown or a
   succession hand-off). It never travels through the archive, through git, or
   through `StorageLocation` config.

   Every command that needs the key reads it from `LEDGER_PAIRING_KEY` and there is
   no `--key` flag: a key in a command line is a key in your shell history and in the
   process table for everyone else on the box. Export it from a file only you can
   read, or paste it into the one shell that needs it.
3. Seal and send a bag:
   ```sh
   export LEDGER_PAIRING_KEY="…"
   ledger mutual-aid seal --root /data --id <record-id> --location partner-a \
     --actor <steward-id>
   ```
   Keep the digest it prints — not the ciphertext, not the key — in your own records.
   That digest is the only thing you need to check the copy later.
4. Schedule an attestation exchange at whatever cadence matches your risk tolerance
   (daily or weekly is reasonable for most collectives). The *holding* partner runs,
   with no key and no archive of their own:
   ```sh
   ledger mutual-aid attest --path /srv/partner-blobs --bag <record-id>
   ```
   and reports the digest back. The owner compares it with the digest from step 3:
   ```sh
   ledger mutual-aid verify --root /data --location partner-a --bag <record-id> \
     --expect <digest-from-step-3>
   ```
   which exits `0` on a match and `1` on a drifted, substituted, or missing copy — so
   it can be the cron job whose failure mail you actually read.
5. Schedule a recovery drill on a cadence that matches your risk tolerance —
   quarterly is a reasonable starting point:
   ```sh
   ledger mutual-aid recover --root /data --location partner-a --bag <record-id> \
     --into /tmp/drill
   ```
   It exits non-zero if the recovered bag does not validate. Treat a failed drill
   exactly like a failed `ledger replicas` check: something to fix before it becomes
   a real loss.

## What this does not do

* **It is not automatic discovery.** Pairing is a deliberate, out-of-band decision
  between two instances that already trust each other's *intent*, if not each
  other's infrastructure — this federates custody, not discovery (EX2 remains the
  place public-record discovery is federated).
* **It does not replace succession planning.** If the key is lost, the sealed
  replica is lost with it — key loss doubles as archive loss for that copy. Fold
  pairing keys into the same succession runbook (`ledger handoff`, `docs/` EX1
  material) a group already keeps for its vault key, so a designated successor can
  actually use a partner's copy rather than staring at ciphertext with no key.
* **It does not keep your seal-time digests for you.** `seal` prints the digest and
  ledger does not store it — deliberately, because the whole check depends on the
  owner holding a copy the partner cannot reach or alter. Writing it into your own
  records (step 3) is a real operational duty, not a formality: without it, `verify`
  has nothing to compare against.
* **It does not solve key custody.** `LEDGER_PAIRING_KEY` keeps the key out of your
  shell history and out of the process table, which is where a `--key` flag would put
  it, but anything that can read the environment of the process can read the key. A
  pairing key deserves the same handling as the vault key: a file only the steward
  account can read, and a place in the succession runbook.
* **It does not vouch for the partner's operational security.** A partner that
  loses your ciphertext, or refuses to return it, has still not read it — but you
  have lost the redundancy. Pair with instances you would trust with your
  *infrastructure*, even though you no longer have to trust them with your
  *content*.
