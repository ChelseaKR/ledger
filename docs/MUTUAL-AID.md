# Mutual preservation aid: encrypted replica exchange (EXP-15)

Community archives run on volunteer labor and borrowed infrastructure. A raided
office, a burned-out collective, or a single steward's hard drive dying can take
years of documentation with it. `ledger.replicate` already lets one archive keep
several of its own copies (`replicate_bag`/`heal`); this document covers the second
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
* **The partner holds ciphertext, not a bag.** `replicate_sealed_bag(bag_dir,
  partner_location, key, agent=..., now=...)` tars the bag, encrypts it, and writes
  a single `<bag-name>.sealed` file to the partner's `StorageLocation`. The
  partner's copy of `ledger` never runs `validate_bag` against it — it cannot, since
  it does not have the key — and it never needs to.
* **Fixity is exchanged as a digest, on a schedule.** `attest_sealed_replica(
  partner_location, bag_name, now=...)` computes the SHA-256 of whatever ciphertext
  bytes are currently on disk. Either side can run it; typically the *holding*
  partner runs it periodically (a cron job, mirroring how `ledger attest-health`
  already works for EXP-01) and reports the digest back out-of-band. The owner
  compares it with `verify_sealed_attestation(expected_sha256, attestation)`
  against the digest `replicate_sealed_bag` returned when the blob was first sent.
  A mismatch means the partner's copy drifted, was substituted, or went missing —
  evidence to act on well before an actual loss forces the question.
* **Recovery is a drill, not an assumption.** `recover_sealed_bag(partner_location,
  bag_name, key, dest_parent)` pulls the ciphertext back, decrypts it locally with
  the key that never left home, and runs it through the same `validate_bag` used
  everywhere else in the archive. Run this periodically against a live partner
  pairing — the "Excellent" bar from the ideation pitch is a full recovery drill on
  commodity hardware, with the partner provably unable to read anything it hosted.

## Setting up a pairing

1. Register the partner as a `mirror` `StorageLocation` the way any replica target
   is registered (`ledger add-location`), pointing at wherever the partner exposes
   storage to you (a synced directory, an SFTP mount, object storage — anything
   `Path` can address once mounted).
2. Generate a pairing key out-of-band, once, with a CSPRNG — for example:
   ```python
   from cryptography.fernet import Fernet
   key = Fernet.generate_key()
   ```
   Exchange it with the partner over a channel you already trust for sensitive
   coordination (the same channel you would use to coordinate a takedown or a
   succession hand-off). It never travels through the archive, through git, or
   through `StorageLocation` config.
3. Seal and send a bag: `replicate_sealed_bag(bag_dir, partner_location, key,
   agent=steward_id, now=now_iso())`. Keep the returned digest — not the
   ciphertext, not the key — in your own records.
4. Schedule an attestation exchange (a cron target on the *holding* side calling
   `attest_sealed_replica`, reported back to the owner) at whatever cadence matches
   your risk tolerance — daily or weekly is reasonable for most collectives.
5. Schedule a recovery drill on a cadence that matches your risk tolerance —
   quarterly is a reasonable starting point — and treat a failed drill exactly like
   a failed `verify_replicas` check: something to fix before it becomes a real loss.

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
* **It does not vouch for the partner's operational security.** A partner that
  loses your ciphertext, or refuses to return it, has still not read it — but you
  have lost the redundancy. Pair with instances you would trust with your
  *infrastructure*, even though you no longer have to trust them with your
  *content*.
