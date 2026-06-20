# infra/ — optional self-host for ledger

This directory lets a community run ledger on **one inexpensive box with no hosted
or cloud dependency**. It is optional: ledger also installs with `pipx install
ledger-archive` and runs directly. Use this when you want the archive running as a
managed, restart-on-failure service behind your own reverse proxy.

Everything the archive needs lives in a single Docker volume on a single machine.
There is no managed database, no object store, no paid service. That is the point:
a collective with no budget can keep the archive — and the records — running on its
own terms, and can walk away with plain BagIt bags at any time.

This is a **runbook for a steward**, written to be honest about what running an
archive actually requires. Read it before you stand one up.

## Contents

- `Dockerfile` — minimal, non-root image; `ledger serve` bound to `0.0.0.0`
  *inside the container only*; `HEALTHCHECK` on `/healthz`.
- `entrypoint.sh` — initializes the archive on first run, then serves it.
- `docker-compose.yml` — one service, one named volume, resource-light, restart
  on failure.
- `.env.example` — documented environment; copy to `.env` and keep it secret.

---

## What a steward is signing up for

Be clear-eyed about this before you start. Running this archive means you are
personally responsible for:

- **The vault key.** If you lose `LEDGER_VAULT_KEY`, every sealed contributor
  identity becomes permanently unreadable. If it leaks, those identities can be
  decrypted. There is no recovery service and no reset.
- **Backups.** Nothing backs this up for you. If the box dies and you have no
  copy, the archive is gone. ledger makes recovery *possible* (plain bags,
  content-addressed store); it does not make backups *happen*.
- **The network edge.** ledger binds to `0.0.0.0` inside the container so the host
  can reach it, but it is **not** hardened to face the open internet directly. You
  must put a reverse proxy in front of it (TLS, rate limiting). The compose file
  publishes only on `127.0.0.1` to make accidental exposure harder.
- **Consent and takedowns.** When a contributor asks to tighten access or be
  removed, you are the one who runs it, across every replica. The tools record
  the decision; you make and execute it.

If no one can hold those responsibilities, do not self-host yet. A laptop running
`ledger` with disciplined backups is more honest than an unmaintained server.

---

## First-time setup

From the **repository root** (the compose build context is the repo root):

1. **Generate a vault key.** This encrypts the contributor identity vault.

   ```sh
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Copy the output. **Back it up separately from the box** (a password manager, a
   sealed offline note) — not on the same disk as the archive.

2. **Create your `.env`.**

   ```sh
   cp infra/.env.example infra/.env
   ```

   Edit `infra/.env`: paste the generated key into `LEDGER_VAULT_KEY`, set
   `LEDGER_ARCHIVE_NAME` to your archive's name, and adjust `LEDGER_PORT` if 8000
   is taken. `infra/.env` is git-ignored — never commit it.

3. **Bring it up.**

   ```sh
   docker compose -f infra/docker-compose.yml up -d --build
   ```

   On first run against an empty volume the entrypoint runs `ledger init`
   automatically, then starts the server. The archive is created with ledger's
   secure defaults: the **narrowest** disclosure policy (`SEALED_UNTIL`), so
   nothing is public until you deliberately open it.

4. **Confirm it is healthy.**

   ```sh
   docker compose -f infra/docker-compose.yml ps          # STATUS should show (healthy)
   curl -fsS http://127.0.0.1:8000/healthz                 # JSON: status + fixity counts
   ```

   `/healthz` reports `status` and fixity counts only (bags audited / passed /
   failed, files checked). It never exposes a bag path, a record id, or any
   identity — it is safe to point a monitor at.

5. **Front it with a reverse proxy.** Terminate TLS and add rate limiting on the
   host, forwarding to `127.0.0.1:<LEDGER_PORT>`. Do not publish the container
   port to the public internet directly.

### Ingesting and serving

The container runs the *read-only browse server*. Write operations (ingest,
policy changes, takedowns) are CLI commands you run against the same volume. Run
them inside the running container so they share the archive and the vault key:

```sh
# Ingest a record, sealing the contributor's name into the vault. Only the opaque
# identity_ref is ever printed back — never the name.
docker compose -f infra/docker-compose.yml exec ledger \
  ledger ingest --root /data --title "Oral history, 1987" \
    --public-field "summary=A first-person account." \
    --contributor-name "Real Name" --actor steward

# List what an anonymous viewer can see, vs. a steward:
docker compose -f infra/docker-compose.yml exec ledger ledger browse --root /data
docker compose -f infra/docker-compose.yml exec ledger ledger browse --root /data --as steward
```

`exec` reuses the running container's environment, so `LEDGER_VAULT_KEY` from
`.env` is present and identity sealing works. If you prefer a one-off container,
use `run --rm` instead — it also reads `.env`.

### Letting contributors submit from the browser (optional)

By default the server is read-only. To let contributors submit a record themselves
(rather than a steward at the CLI), start the server with `--allow-contributions`.
A `/contribute` form then accepts a title, an account, content warnings, an optional
**sealed** contact, and a *requested* visibility:

```sh
# Requires LEDGER_VAULT_KEY so a contributor's sealed contact can be encrypted.
ledger serve --root /data --allow-contributions
```

Safety properties of the contribution path, by construction:

- **Nothing is published by submitting.** Every submission lands *sealed-pending* and
  is queued for review. A steward opens the **`/steward` console** (with a steward
  grant) and **Publishes** it (opening it to the visibility the contributor asked for)
  or **Withholds** it (held for revision) — each choice recorded as an audited event.
  Nothing goes public by inaction. (The CLI `ledger policy` / `takedown` / `cw` still
  work for any out-of-band change.)
- **No-outing.** A contributor's name/contact is optional, sealed into the vault on
  submit, and never echoed on the confirmation page, in a log, or in an error.
- **Off by default.** A read-only deployment never grows a write path unless you opt
  in, and `--allow-contributions` refuses to start without `LEDGER_VAULT_KEY`.

> **Operator note.** The form is an open, unauthenticated POST. On a public-facing
> deployment, put it behind a reverse-proxy rate limit (and/or an invite link or
> CAPTCHA) to deter spam. Binary file upload is intentionally not yet supported on
> this path — stewards attach payload files via `ledger ingest`.

---

## Backups

**This is the most important section. ledger does not back itself up.**

Everything that matters is in the one named volume (`ledger-archive-data`, mounted
at `/data` in the container). Back up the whole volume. The pieces, and what each
is worth:

| Path (under `/data`)        | What it is                                    | Useless without the key? |
|-----------------------------|-----------------------------------------------|--------------------------|
| `store/bags/`               | Every BagIt bag (the records themselves)      | No — records, not identity |
| `store/records/`            | Fast-lookup record manifests                  | No                       |
| `store/logs/`               | Archive-level PREMIS logs (e.g. takedowns)    | No                       |
| `store/config.json`         | Archive configuration                         | No                       |
| `identity.vault`            | **Encrypted** contributor identity vault      | **Yes** — ciphertext only |

Two things must be backed up, and they must be kept **apart**:

1. **The data volume** (the table above). Copy it on a schedule and keep an
   off-site copy.
2. **The vault key** (`LEDGER_VAULT_KEY`). Store it somewhere the data volume's
   backups are *not*, so a single stolen backup cannot both read the records and
   decrypt the identities.

The vault is useless without the key: a leaked `identity.vault` is authenticated
ciphertext (Fernet), so an attacker who has the file but not the key cannot read a
contributor's name. That is by design — but it also means **if you lose the key,
the sealed identities are gone forever.** The bags and records remain readable;
only the identity-to-record links inside the vault are lost.

### Make a backup

```sh
# Snapshot the whole data volume to a timestamped tarball on the host.
docker run --rm \
  -v ledger-archive-data:/data:ro \
  -v "$(pwd)":/backup \
  python:3.12-slim \
  tar czf "/backup/ledger-backup-$(date +%Y%m%d-%H%M%S).tar.gz" -C /data .
```

Move that tarball off the box. Store the vault key separately. Test a restore at
least once — an untested backup is a hope, not a backup. To test it automatically,
extract a backup to a scratch directory and run `ledger verify-backup` against it
(cron-friendly: it exits non-zero if any bag fails fixity, so your scheduler can
alarm):

```sh
# Extract the latest backup somewhere, then verify it restores intact.
mkdir -p /tmp/restore-check && tar xzf <your-backup>.tar.gz -C /tmp/restore-check
ledger verify-backup --backup /tmp/restore-check
```

### Restore

```sh
docker compose -f infra/docker-compose.yml down
docker run --rm \
  -v ledger-archive-data:/data \
  -v "$(pwd)":/backup \
  python:3.12-slim \
  sh -c "rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf /backup/<your-backup>.tar.gz -C /data"
# Ensure the same LEDGER_VAULT_KEY is in infra/.env, then:
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml exec ledger ledger audit --root /data
```

Run a fixity audit after every restore (below) to confirm the bags came back
bit-intact.

---

## Rotating the vault key

Key rotation is a *when*, not an *if* — a steward who held the key leaves, you
suspect the key was exposed, or your community sets a rotation cadence. `ledger
vault rekey` re-encrypts every sealed identity under a new key in one atomic step
and records a `REKEY` PREMIS event in `logs/key-rotations.premis.json`. The refs in
every record are unchanged, so nothing else has to move.

Both keys are passed **through the environment, never on the command line** (a key
in `argv` lands in shell history and the process table): the current key in
`LEDGER_VAULT_KEY` and the new key in `LEDGER_NEW_VAULT_KEY`.

```sh
# Generate the new key and hold it somewhere safe FIRST.
NEW_KEY="$(docker run --rm python:3.12-slim python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Rotate. LEDGER_VAULT_KEY already lives in infra/.env (the current key).
docker compose -f infra/docker-compose.yml exec \
  -e LEDGER_NEW_VAULT_KEY="$NEW_KEY" \
  ledger ledger vault rekey --root /data --actor <your-steward-id>

# On success: replace LEDGER_VAULT_KEY in infra/.env with $NEW_KEY, then restart.
docker compose -f infra/docker-compose.yml up -d
```

The command prints only a count — never a key or an identity. **After it succeeds,
update `LEDGER_VAULT_KEY` to the new key** (and re-do your separately-stored key
backup); the old key no longer opens the vault.

> **One limitation, by design.** If your archive holds *absolute-sealed* content
> (a field or payload sealed from everyone, encrypted at rest under the same key),
> `vault rekey` refuses rather than silently orphaning it — that content needs a
> full re-bagging migration first. Archives that use identity sealing and temporal
> seals (the common case) rotate cleanly.

---

## Adding a replica location

Durability comes from copies in independent places — a member's drive, a second
box, an off-site mirror. Register a mirror so ledger knows about it:

```sh
docker compose -f infra/docker-compose.yml exec ledger \
  ledger add-location --root /data \
    --name offsite-mirror --path /mnt/mirror --kind mirror
```

For ledger to actually write to `/mnt/mirror`, that path must be reachable inside
the container — mount the target into the service in `docker-compose.yml`, e.g.:

```yaml
    volumes:
      - archive-data:/data
      - /mnt/some-host-disk:/mnt/mirror   # a second physical disk, or a remote mount
```

Then check a bag's replica health across all locations:

```sh
docker compose -f infra/docker-compose.yml exec ledger \
  ledger replicas --root /data --id <record_id>
```

Each line shows `ok`/`FAIL` and a per-replica file count — never a payload byte or
an identity. A replica is only as off-site as the disk you point it at: a second
folder on the same drive is not redundancy. Use an independent disk, ideally a
different physical location.

---

## Running a fixity audit

Fixity is checked, not assumed. The audit re-verifies every bag against its
manifests and reports PASS/FAIL. It exits non-zero if any bag fails, so it slots
straight into cron.

```sh
docker compose -f infra/docker-compose.yml exec ledger ledger audit --root /data
```

Read the exit code, not just the text:

```sh
docker compose -f infra/docker-compose.yml exec ledger ledger audit --root /data \
  || echo "FIXITY FAILURE — investigate and heal from a verified replica"
```

Schedule it on the host (example: daily at 03:30):

```cron
30 3 * * *  docker compose -f /path/to/ledger/infra/docker-compose.yml exec -T ledger ledger audit --root /data >> /var/log/ledger-audit.log 2>&1
```

The live `/healthz` endpoint also carries a rolling fixity summary, so your
uptime monitor will turn the box red on drift even between scheduled audits. When
a bag fails, do not overwrite it blindly: heal it by restoring that bag from a
verified replica or backup, then re-run the audit.

---

## Processing a takedown

When a contributor revokes consent, you record the decision and remove the stored
copies. The decision is logged as a PREMIS event *first* (so the audit trail is
complete even if removal is retried), then the bag and the fast-lookup manifest
are deleted from every configured location. A rationale is required and recorded.

```sh
docker compose -f infra/docker-compose.yml exec ledger \
  ledger takedown --root /data --id <record_id> \
    --actor <your-steward-id> --reason "Contributor revoked consent, 2026-06-16"
```

To **tighten** access rather than remove (e.g. move a record back to `sealed`),
use `policy` instead — also rationale-required and logged:

```sh
docker compose -f infra/docker-compose.yml exec ledger \
  ledger policy --root /data --id <record_id> \
    --level sealed-until --actor <your-steward-id> --reason "Contributor request"
```

Honest limits a steward must understand:

- A takedown removes copies from the **locations ledger knows about** (the local
  store and registered mirrors reachable inside the container). Copies on a drive
  that is currently unmounted, or on a mirror you have not registered, are not
  touched until that location is reachable. Track your replicas so a takedown can
  actually reach all of them.
- It does **not** reach **backups** you made (the tarballs above). If a record is
  taken down, prune it from your backup rotation too, or your own backups will
  re-introduce it on restore. This is a manual step. Decide your retention policy
  deliberately.

---

## Upgrade path

1. **Back up first** (see Backups). Always have a known-good copy before upgrading.
2. **Get the new code** (pull the repo, or check out the new release tag).
3. **Rebuild and restart:**

   ```sh
   docker compose -f infra/docker-compose.yml up -d --build
   ```

   The named volume is untouched by a rebuild, so your archive persists across
   the upgrade. `restart: unless-stopped` brings the new container up in place.
4. **Verify after upgrading:**

   ```sh
   docker compose -f infra/docker-compose.yml ps                       # (healthy)
   docker compose -f infra/docker-compose.yml exec ledger ledger audit --root /data
   ```

On the **config schema**: ledger versions its on-disk config and migrates older
files forward in memory on load; a config written by a *newer* ledger than you are
running is refused rather than misread. So upgrading is safe, but **downgrading**
after the config has been touched by a newer build may not be — which is the other
reason to back up before you upgrade.

For production, pin the base image by **digest** in the `Dockerfile` (it is pinned
by tag today, with a comment showing how) so a rebuild cannot pull a changed base
out from under you.

---

## Quick reference

```sh
# Lifecycle
docker compose -f infra/docker-compose.yml up -d --build     # start / upgrade
docker compose -f infra/docker-compose.yml ps                # status + health
docker compose -f infra/docker-compose.yml logs -f ledger    # follow logs (identity-scrubbed)
docker compose -f infra/docker-compose.yml stop              # stop (stays down)
docker compose -f infra/docker-compose.yml down              # stop + remove container (volume kept)

# Operations (all run against the /data volume inside the container)
ledger audit    --root /data                                 # fixity audit (exit non-zero on failure)
ledger replicas --root /data --id <id>                       # replica health for one bag
ledger browse   --root /data [--as steward]                  # what a viewer can see
ledger takedown --root /data --id <id> --actor <s> --reason <why>
ledger policy   --root /data --id <id> --level <lvl> --actor <s> --reason <why>
ledger add-location --root /data --name <n> --path <p> --kind mirror
ledger vault rekey --root /data --actor <s>                   # rotate the vault key (keys via env)

# Dual-control (when config dual_control_threshold > 1): no one steward acts alone
ledger propose  --root /data --action <takedown|unseal|publish> --id <id> --actor <s> --reason <why>
ledger approve  --root /data --id <proposal-id> --actor <other-steward>   # executes when threshold met
ledger proposals --root /data                                # list open proposals
```

To require two stewards for every takedown, identity-unseal, and publish-to-public,
set `"dual_control_threshold": 2` in the archive config. A first steward's `takedown`
(or `propose`) then only *proposes* the action; it runs once a second, distinct
steward `approve`s it. The default of `1` keeps single-steward behaviour.

`docker compose down -v` would delete the volume and the entire archive with it.
There is no `-v` in any command above on purpose.
