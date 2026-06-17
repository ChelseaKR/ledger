# Adopting ledger: deployment-readiness checklist

For an institution or a collective standing up ledger for real records. This is a
one-page checklist drawn directly from the threat model (`docs/THREAT-MODEL.md`), the
governance model (`docs/GOVERNANCE.md`), and the user research. ledger holds the
application-layer line — holding or operating the archive does not out a contributor, and
a corrupt copy never silently becomes the truth — but several controls below are
**operational, not code**, and are your responsibility. Work through them before you
trust ledger with anything that can get someone hurt.

A guiding fact to keep in view throughout: **ledger encrypts contributor *identity* in
the vault, but "sealed" *content* (for example real names or a location in a sealed
field) sits in clear text inside the bag and is readable by ordinary stewards and by
anyone with raw filesystem or replica access.** Confidentiality of content at rest is an
operational control you add, not something the application provides by default (threat
model §4.4, §4.5; user research §7). The final item makes this explicit.

## Host and disk

- [ ] **Full-disk encryption on every host** that holds `store/`, `bags/`, or the vault.
      The disclosure gate is an application-layer control; anyone with raw disk access
      reads content directly (threat model §5). FDE is the floor, and it was a named
      condition from the librarian persona for even a supervised pilot.
- [ ] **The vault key is kept OFF the archive disk.** Supply `LEDGER_VAULT_KEY` by
      environment variable or an external keystore; never an unencrypted env file sitting
      next to the vault. An attacker who seizes both the vault file and the key reads
      every identity — that is total compromise (threat model §4.1, §5). The key never
      belongs in config, on a command line, in a log, or in an error message.
- [ ] **Consider a runtime-entered or keystore-held key** (a passphrase-derived key, an
      external keystore, or a hardware token) so the key is not present on a cold,
      seized box at all (threat model §4.1).

## People and capabilities

- [ ] **A minimum of two stewards** wherever you can manage it, so no single person is
      the only administrator, and so high-stakes actions can require a second steward
      (governance §2).
- [ ] **Separate the steward role from identity-unseal.** A steward grant carries
      `is_steward=True` and an *empty* `identity_unseal` set; administering the archive
      must never confer the power to out a contributor (threat model §2, §4.4; governance
      §1). Grant `identity_unseal` rarely, scoped to named refs, for a stated reason, and
      ideally never as a standing capability held by one person.
- [ ] **Do not concentrate the vault key and an `identity_unseal` grant in one person.**
      A single holder of both can be compelled, or compromised, into outing the
      contributors that grant names (threat model §4.2, §4.4). Split these across people.
- [ ] **Provision grants with least privilege**, and re-key and revoke when a steward
      leaves: removing a steward grant does not undo a key they have already seen
      (governance §2).

## Network and transport

- [ ] **Terminate TLS in front of the stdlib server.** The built-in server speaks plain
      HTTP and binds to `127.0.0.1` by default. A real deployment must sit behind a
      vetted, TLS-terminating reverse proxy before it is exposed beyond loopback (threat
      model §2, §4.6). Without TLS, a network observer reads everything a legitimate
      viewer would at that grant level — never identity, but published/community content
      in clear text.
- [ ] **Expose deliberately.** Only move off loopback once the reverse proxy and TLS are
      in place; a freshly stood-up archive should stay local until you choose otherwise.

## Durability and integrity

- [ ] **Off-box replicas in N independent locations.** Replication is what defends
      availability against a host that drops out, is seized, or refuses to serve; the
      defense is redundancy, not preventing one host from leaving (threat model §4.5).
- [ ] **Scheduled fixity audits across every location.** Run the recurring audit so bit
      rot and tampering are caught before they spread; a divergent copy is quarantined and
      a labelled preservation event is raised, never hidden (threat model §4.5; README).
- [ ] **Back up `store/`, `bags/`, and the vault.** The vault backup is useless without
      its key (that is by design), so it is safe to replicate the ciphertext — but treat
      the *key* as the crown jewel and back it up separately and securely (threat model
      §4.1).
- [ ] **Replicate the moderation/audit log off-box.** The append-only log is append-only
      *as enforced by the application*; an attacker with raw write access can tamper with
      the on-disk file, so off-box copies and fixity matter (threat model §4.4).
- [ ] **Mirror sealed or sensitive content only to hosts you trust with that content.**
      Bags are not encrypted at rest by ledger; a mirror can read the published and
      community-level content it receives. Identity is *not* exposed by mirroring, because
      the vault is not replicated with the bags (threat model §4.5).

## Accessibility and honest gaps

- [ ] **Commission the still-owed independent WCAG 2.2 AA contrast audit.** Contrast is
      currently self-disclosed, not independently certified; the ACR already admits this
      audit is owed (`docs/accessibility/ACR.md`; user research §6). This was a named
      adopter condition.

## Understand what "sealed" means before you rely on it

- [ ] **Know that "sealed" CONTENT is readable by stewards** (and by anyone with raw
      filesystem or replica access) **unless** you use the absolute "sealed-from-everyone"
      tier together with at-rest encryption of the payload. Only contributor *identity* is
      vault-encrypted by default; sealed *content fields* are clear text in the bag
      (threat model §4.4, §4.5; user research §7, §8). A temporal `SEALED_UNTIL` field
      with an `unseal_at` date binds *every* tier, including stewards, until the date
      passes (user research §9) — but an indefinite content seal without at-rest
      encryption is an access-level seal a steward can read. Tell contributors this
      plainly; do not let a vulnerable contributor seal dangerous information under a false
      sense of total secrecy.

---

Most of the residual risk in the threat model is reduced by the operational choices on
this page. ledger holds the line it can hold in code; this checklist is the line you hold
in deployment. Stand up nothing you have not checked, and consult the threat model in full
before you trust the system with records that can endanger the people who made them.
