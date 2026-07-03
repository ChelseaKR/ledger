# Backup runbook — scheduled, encrypted, off-box (RM10)

ledger exists so a community does not lose its records to a single failure or a
seizure. A backup on the same box as the archive defends against neither. This
runbook turns the manual backup obligations in `docs/ADOPTING.md` into two
commands a scheduled job runs unattended:

- `ledger backup` — tar the whole archive, encrypt it, write it where a nightly job
  can copy it off-box;
- `ledger restore-backup` — decrypt, unpack, and **verify** a backup, so a restore
  is proven, not hoped.

Both are cron-friendly: they read secrets from the environment, print only
no-outing-safe summaries, and use a meaningful exit code (`0` on success, non-zero
on failure) so a scheduler can alarm.

> The single most important rule in this document: **the backup passphrase and the
> identity-vault key are two different secrets, and both live off-box, apart from
> the data and from each other.** A stolen backup is ciphertext; it stays ciphertext
> only if the passphrase is not stored next to it.

---

## 1. What is backed up

`ledger backup` tars, then encrypts, the entire archive root:

- **`store/`** — every BagIt bag (payloads + manifests), the records, the PREMIS
  audit logs, and `config.json`;
- **`identity.vault`** — the encrypted contributor-identity vault, **as ciphertext**.
  The backup command never opens or decrypts the vault; it copies the bytes as-is.

The tar is encrypted with a key derived from your **backup passphrase** using the
same scrypt KDF the identity vault uses (ADR 0005; single `cryptography`
dependency), then written with authenticated Fernet as:

```
<dest>/ledger-backup-<UTC-timestamp>.tar.fernet     # the encrypted archive
<dest>/ledger-backup-<UTC-timestamp>.manifest.json  # sidecar: created-at, salt,
                                                    # ciphertext sha256, bag count
```

The sidecar manifest is **not secret** — it holds the KDF salt (a salt need not be
secret), the ciphertext SHA-256 (an integrity check you can re-compute), and counts.
It carries no identity, no payload byte, and no passphrase. Keep it next to its
`.tar.fernet`; `restore-backup` reads the salt from it.

Two layers of encryption are in play, and they are independent:

| Secret | Protects | Where it lives |
|---|---|---|
| **Vault key** (`LEDGER_VAULT_KEY`) | contributor *identities* inside `identity.vault` | off-box, apart from the data |
| **Backup passphrase** (`LEDGER_BACKUP_PASSPHRASE`) | the *whole backup archive* off-box | off-box, apart from the data **and** apart from the vault key |

A host that steals a backup gets nothing without the backup passphrase. Even with
it, the contributor identities inside stay sealed without the *separate* vault key.

---

## 2. Key-backup procedure (do this first, once)

Encryption you cannot recover from is data loss with extra steps. Before you
schedule anything, back up both secrets **off the box**:

1. **Generate a strong backup passphrase.** Use a long, random passphrase (a
   passphrase manager or `openssl rand -base64 24`). This is the key to every
   off-box backup you will ever make.
2. **Write it down, off-box, in two separate places.** On paper in a locked
   location, and/or in a password manager a second steward controls. Do **not**
   store it in `infra/.env`, in the repo, in the same cloud bucket as the backups,
   or next to the `.tar.fernet` files. If it lives next to the backup, the
   encryption bought you nothing.
3. **Confirm the vault key is already backed up separately.** `LEDGER_VAULT_KEY`
   protects contributor identities and must be held apart from both the data and
   the backup passphrase (`docs/ADOPTING.md` → durability; threat model §4.1). The
   two secrets should not be recoverable from the same place — that is what makes a
   single seizure or compromise non-catastrophic.
4. **Record where each secret lives** in your continuity notes (`docs/CONTINUITY.md`
   → "If the project goes dormant") so a successor steward can find them. Record the
   *location*, never the secret itself.

Rotating the backup passphrase: pick a new passphrase, take a fresh `ledger backup`
under it, verify a restore (below), then retire old backups per your retention
policy. Old backups remain readable only under the old passphrase, so keep it until
those backups have aged out.

---

## 3. Schedule a nightly backup (documented cron)

ledger ships **no daemon** for this — the archive is meant to run on one cheap box,
and the OS scheduler is already there. Use cron (or a systemd timer).

Provide both secrets to the job's environment, never on the command line (a secret
in `argv` lands in shell history and the process table). A small wrapper keeps the
crontab clean:

```sh
#!/bin/sh
# /usr/local/bin/ledger-nightly-backup.sh  — chmod 700, owned by the ledger user.
set -eu
export LEDGER_VAULT_KEY="$(cat /root/secrets/ledger-vault.key)"
export LEDGER_BACKUP_PASSPHRASE="$(cat /root/secrets/ledger-backup.pass)"

# 1. Take a fresh encrypted backup and keep only the 14 newest locally.
ledger backup --root /srv/ledger --dest /srv/ledger-backups --keep 14

# 2. Copy it OFF the box (rsync/rclone/restic to a location you control). This is
#    the step that actually defends against seizure — a local backup is not off-box.
rsync -a --delete /srv/ledger-backups/ offbox:/backups/ledger/
```

Crontab (run at 02:30 nightly; mail non-zero exits so a failure alarms):

```cron
MAILTO=steward@example.org
30 2 * * *  /usr/local/bin/ledger-nightly-backup.sh
```

systemd-timer equivalent (if you prefer timers to cron):

```ini
# /etc/systemd/system/ledger-backup.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/ledger-nightly-backup.sh
# Load secrets from a root-only file (chmod 600), never from the unit text:
EnvironmentFile=/root/secrets/ledger-backup.env

# /etc/systemd/system/ledger-backup.timer
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

> **Docker / compose users:** run the wrapper on the **host** against the mounted
> data volume (`docker compose exec` or a host `ledger` install pointed at the bind
> mount). `infra/docker-compose.yml` carries a commented, opt-in example sidecar for
> the pattern, but host cron is the recommended, minimal-computing default.

---

## 4. Restore drills — an untested backup is a hope

A backup you have never restored is a guess. `restore-backup` decrypts, unpacks,
**and then runs the same readability + RFC 8493 fixity checks as `verify-backup`**,
so a passing drill proves the backup would actually recover the archive:

```sh
export LEDGER_BACKUP_PASSPHRASE="$(cat /root/secrets/ledger-backup.pass)"
export LEDGER_VAULT_KEY="$(cat /root/secrets/ledger-vault.key)"   # for the vault-readable check

ledger restore-backup \
  --archive /srv/ledger-backups/ledger-backup-20260703T013013Z.tar.fernet \
  --target  /tmp/restore-drill
echo "exit: $?"   # 0 = every restored bag passed fixity; non-zero = alarm
```

`restore-backup` already verifies, so a drill is one command. If you restore by some
other path, run `ledger verify-backup --backup /tmp/restore-drill` afterward to get
the same proof.

**Cadence.** Run a restore drill on a schedule, not only after a disaster:

- **Monthly** at minimum — a quick automated drill into a throwaway directory,
  exit code checked (the whole cycle is covered by `tests/test_backup_restore.py`
  and `tests/test_encrypted_backup.py`, but a drill exercises *your real backups*).
- **After any change** to the box, the ledger version, or the backup passphrase.
- **After a passphrase rotation**, before retiring the old passphrase.

A wrong passphrase or a tampered archive fails the drill with a clear,
no-outing-safe error (`backup decryption failed (wrong passphrase or tampered
archive)`) rather than yielding garbage — Fernet authenticates the ciphertext.

---

## 5. Retention / prune

`--keep N` on `ledger backup` prunes older backups in `--dest` to the *N* newest
(archive + its sidecar removed together), so an unattended nightly job does not fill
the disk. `keep` must be positive — the tool refuses to delete everything.

Choose retention for your recovery window: e.g. `--keep 14` for two weeks of nightly
local copies, with your off-box copy (step 3) keeping a longer tail under its own
lifecycle rules. Retention applies to the **local** `--dest`; set the off-box
target's retention in your rsync/rclone/restic policy.

---

## 6. Quick reference

```sh
# nightly, cron-friendly (secrets from the environment):
ledger backup --root <archive-root> --dest <dir> [--keep N]

# restore drill — decrypts, unpacks, and verifies in one step:
ledger restore-backup --archive <file>.tar.fernet --target <empty-dir>

# verify an already-restored tree (e.g. one restored by hand):
ledger verify-backup --backup <restored-archive-root>
```

Related: `docs/ADOPTING.md` (durability checklist), `docs/CONTINUITY.md` (dormancy
and successor handoff), `docs/THREAT-MODEL.md` §4.1/§4.5 (key handling, off-box
redundancy), and `docs/adr/` ADR 0005 (stdlib-first, single `cryptography`
dependency).
