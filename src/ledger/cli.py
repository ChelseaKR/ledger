"""The ``ledger`` command-line entry point — one discoverable surface for stewards.

A single :func:`main` dispatches a small, explicit set of subcommands over the
:class:`~ledger.ingest.Archive` facade and the disclosure, replication, and
moderation layers. Two qualities shape every choice here:

* **Operability / usability** — each subcommand has its own ``--help``, the flags
  are predictable (``--root`` everywhere, ``--as`` to choose a viewer), and the
  exit code is meaningful: ``0`` on success, non-zero on any error, with the
  failure printed to *stderr* so scripts can branch on it.
* **Safety (the no-outing rule)** — the CLI is a read/write boundary, so it is
  held to the same rule as every other surface. A contributor name or contact is
  accepted only as ingest *input*; it is sealed into the vault and the CLI then
  prints *only* the opaque ``identity_ref`` token, never echoing the name back.
  No subcommand ever writes an identity to stdout, stderr, or a log line.

Determinism: every command that stamps time accepts ``--now`` (an ISO-8601
string) and otherwise falls back to :func:`ledger.models.now_iso`, so a scripted
or golden run is reproducible (the demo relies on this).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ledger import acr_gen, demo
from ledger.access.grants import anonymous, community_member, steward
from ledger.config import Config, StorageLocation
from ledger.errors import LedgerError
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    Grant,
    PremisEvent,
    Record,
    now_iso,
)
from ledger.moderate import add_content_warning, change_consent, takedown
from ledger.replicate import verify_replicas
from ledger.server import serve

_CONFIG_FILENAME = "config.json"
_PREMIS_FILENAME = "premis.json"


# --- shared helpers ---------------------------------------------------------


def _load_config(root: Path) -> Config:
    """Load the archive configuration that lives under ``root/store``.

    The path is derived the same way :meth:`Archive.init` writes it, so ``--root``
    is the one location a steward must remember (usability).
    """
    return Config.load(root / "store" / _CONFIG_FILENAME)


def _open_archive(root: Path) -> Archive:
    """Open an existing archive rooted at ``root`` (load config, wire the facade)."""
    return Archive(_load_config(root))


def _grant_for(subject: str | None) -> Grant:
    """Resolve a CLI ``--as`` subject to a least-privilege grant.

    The vocabulary is deliberately tiny and predictable: an absent subject (or
    the literal ``anonymous``) is the narrowest public grant; ``steward`` is the
    administrative grant; anything else is treated as a named community member
    (deny by default — a CLI flag never confers identity-unseal power).
    """
    if subject is None or subject == "anonymous":
        return anonymous()
    if subject == "steward":
        return steward("cli-steward")
    return community_member(subject)


def _parse_pairs(pairs: Sequence[str]) -> list[tuple[str, str]]:
    """Parse ``name=value`` strings into ordered ``(name, value)`` tuples.

    A malformed pair (no ``=``) raises :class:`~ledger.errors.LedgerError` naming
    only the offending key shape, never any value (no-outing rule).
    """
    out: list[tuple[str, str]] = []
    for raw in pairs:
        name, sep, value = raw.partition("=")
        if not sep or not name:
            raise LedgerError("a field must be given as name=value")
        out.append((name, value))
    return out


# --- subcommand implementations --------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    """``init`` — create a fresh archive under ``--root`` with secure defaults."""
    root = Path(args.root)
    config = Config.default(args.name, root)
    Archive.init(config)
    print(f"initialized archive {args.name!r} at {root}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """``ingest`` — build a record (and optional sealed identity) and store it.

    Public descriptive fields are published; sealed fields default to the
    narrowest policy. If a content warning is given it is attached as structured
    metadata. A contributor name/contact, when supplied, is sealed into the vault
    and *only* the resulting opaque ``identity_ref`` is printed — the name is
    never echoed (no-outing rule).
    """
    archive = _open_archive(Path(args.root))

    fields: list[Field] = []
    for name, value in _parse_pairs(args.public_field or []):
        fields.append(Field(name=name, value=value, policy=AccessPolicy.PUBLIC))
    for name, value in _parse_pairs(args.sealed_field or []):
        fields.append(Field(name=name, value=value, policy=AccessPolicy.SEALED_UNTIL))

    record = Record(
        title=args.title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=[args.title],
            publisher=[archive.config.archive_name],
        ),
        fields=fields,
        content_warnings=list(args.cw or []),
    )

    payload: dict[str, Path] = {}
    for file_arg in args.files or []:
        source = Path(file_arg)
        payload[source.name] = source

    identity: ContributorIdentity | None = None
    # Seal whenever ANY contributor material is supplied, so a contact given without
    # a name is never silently dropped (data loss of sensitive input -> safety).
    if args.contributor_name or args.contributor_contact:
        identity = ContributorIdentity(
            name=args.contributor_name or "",
            contact=args.contributor_contact or "",
        )

    now = args.now if args.now else now_iso()
    aip = archive.ingest(payload, record, identity=identity, agent=args.actor, now=now)

    print(f"record_id: {record.record_id}")
    print(f"bag: {aip.bag.path}")
    if record.identity_ref is not None:
        # Print ONLY the opaque token; never the contributor's name or contact.
        print(f"identity_ref: {record.identity_ref}")
    return 0


def _cmd_browse(args: argparse.Namespace) -> int:
    """``browse`` — print the titles a grant may list, one per line."""
    archive = _open_archive(Path(args.root))
    grant = _grant_for(args.as_subject)
    now = args.now if args.now else now_iso()
    disclosed = archive.browse(grant, now=now)
    for record in disclosed:
        print(f"{record.record_id}\t{record.title}")
    print(f"({len(disclosed)} record(s) visible to {grant.subject})")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """``show`` — print one record's disclosed (safe) shape as JSON."""
    archive = _open_archive(Path(args.root))
    grant = _grant_for(args.as_subject)
    now = args.now if args.now else now_iso()
    disclosed = archive.disclose(args.id, grant, now=now)
    print(json.dumps(disclosed.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """``serve`` — run the accessible browse server (blocking) on host/port."""
    if not 0 <= args.port <= 65535:
        # Reject out-of-range ports with a clean error instead of letting the socket
        # layer raise an uncaught OverflowError (operability — predictable failure).
        raise LedgerError(f"port out of range (0-65535): {args.port}")
    archive = _open_archive(Path(args.root))
    grants_path = Path(args.grants) if args.grants else None
    if args.allow_contributions and not os.environ.get("LEDGER_VAULT_KEY"):
        # The contribution form offers an optional sealed contact, which must be
        # encrypted into the vault on submit. Refuse to enable the write path without
        # a key rather than risk dropping a contributor's sealed details (safety).
        raise LedgerError(
            "--allow-contributions requires LEDGER_VAULT_KEY so contributor contact "
            "details can be sealed into the vault"
        )
    print(f"serving {archive.config.archive_name!r} on http://{args.host}:{args.port}")
    serve(
        archive,
        host=args.host,
        port=args.port,
        grants_path=grants_path,
        allow_contributions=args.allow_contributions,
    )
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """``audit`` — validate every bag's fixity and print a PASS/FAIL summary.

    Returns non-zero if any bag fails, so a cron or CI gate can branch on the
    exit code (operability, failure transparency). Only bag names and counts are
    printed — never a payload byte or an identity (no-outing rule).
    """
    archive = _open_archive(Path(args.root))
    reports = archive.audit_fixity()
    failures = 0
    for name, report in reports:
        # A structurally broken bag arrives here as a report with a failing result
        # (audit_fixity no longer aborts the sweep), so it shows as FAIL and the
        # remaining bags are still audited (degradability, failure transparency).
        ok = report.ok
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}\t{name}\t({report.checked} file(s) checked)")
    summary = "PASS" if failures == 0 else "FAIL"
    print(f"{summary}: {len(reports)} bag(s) audited, {failures} failed")
    return 0 if failures == 0 else 1


def _persist_record(archive: Archive, record: Record, event: PremisEvent) -> None:
    """Persist an updated record manifest and PREMIS event via the archive.

    Thin wrapper over :meth:`Archive.apply_update` (the shared write path) so the
    CLI and the server persist post-ingest changes identically (no-outing rule is
    enforced once, in one place).
    """
    archive.apply_update(record, event)


def _cmd_policy(args: argparse.Namespace) -> int:
    """``policy`` — record an accountable consent/policy change and persist it.

    Routes through :func:`ledger.moderate.change_consent` (which requires a
    rationale) so the change is justified and attributed, then persists the new
    record manifest and the PREMIS event (autonomy, accountability).
    """
    archive = _open_archive(Path(args.root))
    record = archive.get(args.id)
    now = args.now if args.now else now_iso()
    try:
        level = AccessPolicy(args.level)
    except ValueError as exc:
        raise LedgerError(f"unknown access level: {args.level!r}") from exc
    updated, event, action = change_consent(
        record, level, actor=args.actor, reason=args.reason, now=now
    )
    _persist_record(archive, updated, event)
    print(f"policy for {args.id} changed to {level.value} by {action.actor}")
    return 0


def _cmd_cw(args: argparse.Namespace) -> int:
    """``cw`` — add a content warning to an existing record, after publication.

    Content warnings were creation-only, which meant harm surfacing after a record
    was published could not be flagged (user research P1-2). This records an
    accountable ``warn`` moderation decision and persists the warning so the next
    render shows it before the material (safety, accountability).
    """
    archive = _open_archive(Path(args.root))
    record = archive.get(args.id)
    now = args.now if args.now else now_iso()
    updated, event, action = add_content_warning(
        record, args.warning, actor=args.actor, reason=args.reason, now=now
    )
    _persist_record(archive, updated, event)
    print(f"content warning {args.warning!r} added to {args.id} by {action.actor}")
    return 0


def _cmd_takedown(args: argparse.Namespace) -> int:
    """``takedown`` — record an accountable takedown and remove stored copies.

    Order matters and is now honoured: (1) the accountable decision is recorded and
    durably persisted FIRST, so the audit trail of *why* survives even if removal is
    interrupted or retried; (2) the contributor identity is revoked from the vault
    (right to be forgotten); (3) every stored copy is deleted from the bag, the
    fast-lookup manifest, and each configured location. Only the record id and
    counts are printed (no-outing rule).
    """
    import shutil

    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    event, action = takedown(args.id, actor=args.actor, reason=args.reason, now=now)

    # Capture the sealed identity ref BEFORE any deletion, so step 2 can revoke it.
    identity_ref: str | None = None
    try:
        identity_ref = archive.get(args.id).identity_ref
    except LedgerError:
        identity_ref = None

    # 1. Record and DURABLY persist the decision first (accountability — the record
    #    of why a takedown happened must outlive the data it concerns).
    log_path = archive.logs_dir / "takedowns.premis.json"
    archive.logs_dir.mkdir(parents=True, exist_ok=True)
    log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
    log.record(event)
    log.write(log_path)

    # 2. Revoke the contributor identity from the vault. A vault that cannot be
    #    opened is NOT skipped silently — warn so a steward can revoke by hand
    #    (consent / right to be forgotten, failure transparency).
    revoked = False
    if identity_ref is not None:
        try:
            archive._open_vault(None).revoke(identity_ref)
            revoked = True
        except LedgerError as exc:
            print(
                f"warning: could not revoke identity from the vault ({exc}); "
                "revoke it manually to complete the takedown",
                file=sys.stderr,
            )

    # 3. Remove every stored copy.
    removed = 0
    bag_dir = archive.bags_dir / args.id
    if bag_dir.exists():
        shutil.rmtree(bag_dir)
        removed += 1
    fast = archive.records_dir / f"{args.id}.json"
    if fast.exists():
        fast.unlink()

    for location in archive.config.locations:
        replica = Path(location.path) / args.id
        if replica.exists() and replica != bag_dir:
            shutil.rmtree(replica)
            removed += 1

    suffix = "; identity revoked" if revoked else ""
    print(f"record {args.id} taken down by {action.actor}; {removed} copy(ies) removed{suffix}")
    return 0


def _cmd_replicas(args: argparse.Namespace) -> int:
    """``replicas`` — report the health of one bag's replicas across locations.

    A convenience read over :func:`ledger.replicate.verify_replicas`: one line
    per location with ``ok``/``FAIL`` and a per-replica file count, never a
    payload byte (inspectability, no-outing rule).
    """
    archive = _open_archive(Path(args.root))
    statuses = verify_replicas(args.id, archive.config.locations)
    for status in statuses:
        flag = "ok" if status.ok else "FAIL"
        print(f"{flag}\t{status.location}\t({status.report.checked} file(s) checked)")
    return 0


def _cmd_add_location(args: argparse.Namespace) -> int:
    """``add-location`` — register a mirror target in the archive config.

    Lets a steward add redundancy declaratively; the config is re-saved atomically
    so a crash mid-write cannot strand a half-written file (administrability,
    integrity).
    """
    root = Path(args.root)
    config = _load_config(root)
    config.locations.append(StorageLocation(name=args.name, path=args.path, kind=args.kind))
    config.save(root / "store" / _CONFIG_FILENAME)
    print(f"added {args.kind} location {args.name!r} at {args.path}")
    return 0


def _cmd_vault_rekey(args: argparse.Namespace) -> int:
    """``vault rekey`` — rotate the identity-vault key, re-encrypting every identity.

    Both keys travel as environment variables, never on the command line: the
    current key in ``LEDGER_VAULT_KEY`` and the new key in ``LEDGER_NEW_VAULT_KEY``
    (confidentiality — a key in argv would land in shell history and the process
    table). The rotation is atomic and records a ``REKEY`` PREMIS event; only a
    count is printed, never a key or an identity (no-outing rule). After it
    succeeds, the steward sets ``LEDGER_VAULT_KEY`` to the new key going forward.
    """
    archive = _open_archive(Path(args.root))
    new_raw = os.environ.get("LEDGER_NEW_VAULT_KEY")
    if not new_raw:
        raise LedgerError(
            "set LEDGER_NEW_VAULT_KEY to the new vault key (via the environment, never argv)"
        )
    old_raw = os.environ.get("LEDGER_VAULT_KEY")
    now = args.now if args.now else now_iso()
    count = archive.rekey_vault(
        new_raw.encode("ascii"),
        old_key=old_raw.encode("ascii") if old_raw else None,
        agent=args.actor,
        now=now,
    )
    print(f"rekeyed {count} identity(ies); set LEDGER_VAULT_KEY to the new key going forward")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """``demo`` — run the self-contained, scripted no-outing proof end to end."""
    return demo.main()


def _cmd_acr(args: argparse.Namespace) -> int:
    """``acr`` — print the Accessibility Conformance Report (VPAT 2.5) as Markdown."""
    print(acr_gen.render())
    return 0


# --- parser construction ----------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser with one subparser per command.

    Each subcommand gets its own ``--help`` and a ``--now`` where it stamps time,
    so the surface is discoverable and reproducible (operability, determinism).
    """
    parser = argparse.ArgumentParser(
        prog="ledger",
        description="A privacy-first community archive for queer histories and mutual-aid knowledge.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p_init = sub.add_parser("init", help="create a fresh archive")
    p_init.add_argument("--root", required=True, help="archive root directory")
    p_init.add_argument("--name", required=True, help="archive name")
    p_init.set_defaults(func=_cmd_init)

    p_ingest = sub.add_parser("ingest", help="ingest an item")
    p_ingest.add_argument("--root", required=True)
    p_ingest.add_argument("--title", required=True)
    p_ingest.add_argument("files", nargs="*", help="payload files to ingest")
    p_ingest.add_argument(
        "--public-field", action="append", metavar="name=value", help="a PUBLIC field"
    )
    p_ingest.add_argument(
        "--sealed-field", action="append", metavar="name=value", help="a SEALED field"
    )
    p_ingest.add_argument("--cw", action="append", metavar="WARNING", help="content warning")
    p_ingest.add_argument("--contributor-name", help="sealed into the vault; never printed back")
    p_ingest.add_argument("--contributor-contact", help="sealed into the vault")
    p_ingest.add_argument("--actor", default="ledger", help="ingest agent id")
    p_ingest.add_argument("--now", help="ISO-8601 timestamp for reproducible ingest")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_browse = sub.add_parser("browse", help="list records a viewer may see")
    p_browse.add_argument("--root", required=True)
    p_browse.add_argument("--as", dest="as_subject", help="viewer subject (default: anonymous)")
    p_browse.add_argument("--now", help="ISO-8601 timestamp")
    p_browse.set_defaults(func=_cmd_browse)

    p_show = sub.add_parser("show", help="show one record as disclosed JSON")
    p_show.add_argument("--root", required=True)
    p_show.add_argument("--id", required=True)
    p_show.add_argument("--as", dest="as_subject", help="viewer subject (default: anonymous)")
    p_show.add_argument("--now", help="ISO-8601 timestamp")
    p_show.set_defaults(func=_cmd_show)

    p_serve = sub.add_parser("serve", help="run the accessible browse server")
    p_serve.add_argument("--root", required=True)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--grants", help="path to a pre-provisioned grants JSON file")
    p_serve.add_argument(
        "--allow-contributions",
        action="store_true",
        help="enable the /contribute submission form (requires LEDGER_VAULT_KEY)",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_audit = sub.add_parser("audit", help="validate every bag's fixity")
    p_audit.add_argument("--root", required=True)
    p_audit.set_defaults(func=_cmd_audit)

    p_policy = sub.add_parser("policy", help="record an accountable consent/policy change")
    p_policy.add_argument("--root", required=True)
    p_policy.add_argument("--id", required=True)
    p_policy.add_argument("--level", required=True, help="new default access level")
    p_policy.add_argument("--actor", required=True, help="steward id making the change")
    p_policy.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_policy.add_argument("--now", help="ISO-8601 timestamp")
    p_policy.set_defaults(func=_cmd_policy)

    p_cw = sub.add_parser("cw", help="add a content warning to an existing record")
    p_cw.add_argument("--root", required=True)
    p_cw.add_argument("--id", required=True)
    p_cw.add_argument("--warning", required=True, help="the content-warning tag to add")
    p_cw.add_argument("--actor", required=True, help="steward id making the change")
    p_cw.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_cw.add_argument("--now", help="ISO-8601 timestamp")
    p_cw.set_defaults(func=_cmd_cw)

    p_takedown = sub.add_parser("takedown", help="record a takedown and remove copies")
    p_takedown.add_argument("--root", required=True)
    p_takedown.add_argument("--id", required=True)
    p_takedown.add_argument("--actor", required=True, help="steward id")
    p_takedown.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_takedown.add_argument("--now", help="ISO-8601 timestamp")
    p_takedown.set_defaults(func=_cmd_takedown)

    p_replicas = sub.add_parser("replicas", help="report a bag's replica health")
    p_replicas.add_argument("--root", required=True)
    p_replicas.add_argument("--id", required=True)
    p_replicas.set_defaults(func=_cmd_replicas)

    p_loc = sub.add_parser("add-location", help="register a storage/mirror location")
    p_loc.add_argument("--root", required=True)
    p_loc.add_argument("--name", required=True)
    p_loc.add_argument("--path", required=True)
    p_loc.add_argument("--kind", default="mirror", choices=["local", "mirror"])
    p_loc.set_defaults(func=_cmd_add_location)

    p_vault = sub.add_parser("vault", help="identity-vault maintenance")
    vault_sub = p_vault.add_subparsers(dest="vault_command", required=True, metavar="SUBCOMMAND")
    p_rekey = vault_sub.add_parser(
        "rekey", help="rotate the vault key (keys via env vars, never argv)"
    )
    p_rekey.add_argument("--root", required=True)
    p_rekey.add_argument("--actor", required=True, help="steward id performing the rotation")
    p_rekey.add_argument("--now", help="ISO-8601 timestamp")
    p_rekey.set_defaults(func=_cmd_vault_rekey)

    p_demo = sub.add_parser("demo", help="run the scripted end-to-end no-outing proof")
    p_demo.set_defaults(func=_cmd_demo)

    p_acr = sub.add_parser("acr", help="print the Accessibility Conformance Report")
    p_acr.set_defaults(func=_cmd_acr)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the chosen subcommand, returning an exit code.

    Returns ``0`` on success and a non-zero code on any handled error, printing
    the failure to *stderr* (operability — predictable exit codes). A
    :class:`~ledger.errors.LedgerError` is rendered as a one-line message that, by
    the project's threat model, names only the condition and at most an object id —
    never a contributor identity or a sealed value (no-outing rule).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
