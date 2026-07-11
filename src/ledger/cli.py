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

from ledger import acr_gen, attest, demo, dualcontrol, preservation, succession
from ledger.access.grants import (
    anonymous,
    community_member,
    issue_grant_token,
    load_grants,
    load_revocations,
    revoke_subject,
    steward,
)
from ledger.access.redaction import redact_field, redact_payload
from ledger.config import Config, StorageLocation
from ledger.errors import LedgerError
from ledger.export_drive import build_export_drive
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.lockdown import (
    execute_lockdown,
    execute_stand_up,
    is_locked_down,
    plan_lockdown,
    verify_backup_location,
)
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    Field,
    Grant,
    HashAlgo,
    PayloadFile,
    PremisEvent,
    Record,
    now_iso,
)
from ledger.moderate import (
    add_content_warning,
    change_consent,
    execute_takedown,
    set_field_policy,
    set_payload_policy,
)
from ledger.oralhistory import apply_session_manifest, parse_session_manifest
from ledger.print_edition import build_print_edition
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


# Pre-existing complexity (one function branches over every CLI ingest option);
# surfaced 2026-07-05 when CQ-05's complexity gate was enabled. Waived, not
# re-muted: tracked for a follow-up split (see ledger-REMEDIATION.md P3-2).
def _cmd_ingest(args: argparse.Namespace) -> int:  # noqa: C901
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

    # A transcript/caption makes audio or video accessible to a Deaf or hard-of-
    # hearing reader (user research H3). Pre-declare the payload carrying it so the
    # one ingest path preserves the transcript (it recomputes the address/size). The
    # media type is guessed so an audio/video file is recognised as such.
    import mimetypes

    predeclared: list[PayloadFile] = []
    for fname, text in _parse_pairs(args.transcript or []):
        guessed, _ = mimetypes.guess_type(fname)
        predeclared.append(
            PayloadFile(
                filename=fname,
                address=ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64),
                media_type=guessed or "application/octet-stream",
                policy=record.default_policy,
                transcript=text,
            )
        )
    if predeclared:
        record.payloads = predeclared

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
    # Accessibility advisory: audio/video without a transcript is unusable to a Deaf
    # or hard-of-hearing reader. Nudge, do not block (user research H3 / WCAG 1.2).
    for p in record.payloads:
        if p.media_type.startswith(("audio/", "video/")) and not p.transcript:
            print(
                f"note: {p.filename} is audio/video with no transcript; add one with "
                f"--transcript '{p.filename}=...' so it is accessible (WCAG 1.2)",
                file=sys.stderr,
            )
    # Preservation-planning advisory: an obsolescent/proprietary format may verify by
    # fixity yet become unreadable over time. Nudge toward migration, do not block
    # (OAIS Preservation Planning; NDSA Levels). The PREMIS log already records this.
    for file_arg in args.files or []:
        src = Path(file_arg)
        fmt = preservation.identify_file(src)
        if fmt.at_risk:
            print(
                f"note: {src.name} is {fmt.name}, an at-risk/obsolescent format. "
                f"{fmt.recommendation}",
                file=sys.stderr,
            )
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
    revocations_path = Path(args.revocations) if args.revocations else None
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
        revocations_path=revocations_path,
        allow_contributions=args.allow_contributions,
    )
    return 0


def _grant_secret_from_env() -> bytes:
    """The grant-token signing secret from the environment, or a clean error.

    Like the vault key, the secret travels only in ``LEDGER_GRANT_SECRET`` — never
    on the command line, where it would land in shell history and the process table
    (confidentiality). An unset secret is a hard error rather than a silent
    unsigned token (fail closed)."""
    raw = os.environ.get("LEDGER_GRANT_SECRET")
    if not raw:
        raise LedgerError(
            "set LEDGER_GRANT_SECRET to the grant signing secret (via the environment, never argv)"
        )
    return raw.encode("utf-8")


def _cmd_grant_issue(args: argparse.Namespace) -> int:
    """``grant issue`` — mint an authenticated capability token for a subject.

    The token is the *only* thing printed: it is a sealed bearer value, so the
    signing secret is never echoed and nothing else is written to stdout (the
    no-outing rule's no-secret-outing corollary). The subject must already have a
    provisioned grant in the grants file for the token to authorise anything at the
    server; issuing a token for an unprovisioned subject is harmless (it resolves to
    anonymous), so this command does not require the grants file. ``--expires-at``
    bounds the token to an ISO-8601 instant; omitted, the token does not expire."""
    secret = _grant_secret_from_env()
    token = issue_grant_token(args.subject, secret, expires_at=args.expires_at or "")
    print(token)
    return 0


def _cmd_grant_revoke(args: argparse.Namespace) -> int:
    """``grant revoke`` — add a subject to the revocation list.

    Retracts every still-unexpired token for the subject the next time the server
    reads the list, without rotating the shared secret (immediate, targeted
    retraction). Idempotent. Never prints or needs the signing secret."""
    path = Path(args.revocations)
    revoke_subject(path, args.subject)
    print(f"revoked {args.subject!r}; wrote {path}")
    return 0


def _cmd_grant_list(args: argparse.Namespace) -> int:
    """``grant list`` — list provisioned grant subjects with their revoked flag.

    Reads the grants file and the revocation list and prints one subject per line
    with a ``[revoked]`` marker where applicable. Subjects that are revoked but no
    longer provisioned are listed too, so a steward can see a dangling revocation.
    Prints no token and no secret (only public subject identifiers)."""
    grants_path = Path(args.grants) if args.grants else None
    grants = load_grants(grants_path) if grants_path is not None else {}
    revocations_path = Path(args.revocations) if args.revocations else None
    revoked = load_revocations(revocations_path) if revocations_path is not None else set()
    subjects = sorted(set(grants) | revoked)
    if not subjects:
        print("(no grants provisioned)")
        return 0
    for subject in subjects:
        marker = " [revoked]" if subject in revoked else ""
        provisioned = "" if subject in grants else " [not provisioned]"
        print(f"{subject}{marker}{provisioned}")
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


def _cmd_seal(args: argparse.Namespace) -> int:
    """``seal`` — set the disclosure policy of one field, payload, or the record default.

    The first-class, accountable workflow for *applying* a disclosure policy to an
    already-archived item — embargo, conditional release, or a plain visibility level
    — without re-ingesting it. It is the steward-facing complement to the per-field
    policies a contributor chooses at ingest: a steward can later embargo a name until
    a date (``--field name --level sealed-until --until 2035-01-01``), seal a payload
    to stewards, or move the whole record's default (``--default``).

    Exactly one target is required (``--field`` / ``--payload`` / ``--default``).
    ``--until`` (a temporal embargo) and ``--condition`` apply only to a *field*, since
    only a field carries an unseal date/condition; using them with another target is a
    clean error. Every change routes through the audited moderation layer (a rationale
    is required) and persists the new manifest plus a PREMIS event (autonomy,
    accountability, the no-outing rule).
    """
    archive = _open_archive(Path(args.root))
    record = archive.get(args.id)
    now = args.now if args.now else now_iso()
    try:
        level = AccessPolicy(args.level)
    except ValueError as exc:
        raise LedgerError(f"unknown access level: {args.level!r}") from exc

    if (args.until or args.condition) and not args.field:
        raise LedgerError("--until/--condition apply only to a --field target")

    if args.field:
        updated, event, action = set_field_policy(
            record,
            args.field,
            level,
            unseal_at=args.until,
            unseal_condition=args.condition,
            actor=args.actor,
            reason=args.reason,
            now=now,
        )
        target = f"field {args.field!r}"
    elif args.payload:
        updated, event, action = set_payload_policy(
            record, args.payload, level, actor=args.actor, reason=args.reason, now=now
        )
        target = f"payload {args.payload!r}"
    else:
        updated, event, action = change_consent(
            record, level, actor=args.actor, reason=args.reason, now=now
        )
        target = "default policy"
    _persist_record(archive, updated, event)
    print(f"{target} for {args.id} set to {level.value} by {action.actor}")
    return 0


def _cmd_redact(args: argparse.Namespace) -> int:
    """``redact`` — apply a recorded redaction to a stored field or payload.

    Unlike ``seal`` (which gates *visibility* but keeps the value at rest for a future
    authorized viewer), a redaction is a destructive *transform*: it replaces a field's
    value with ``[redacted]`` or drops a payload from the manifest, then persists the
    lossy copy. It is the tool for content that must never be served again — a name a
    contributor asked to be erased — not merely held back. The change is recorded as a
    PREMIS ``REDACTION`` event naming only the field/filename (never the removed value),
    so the redaction is provable after the fact (auditability, the no-outing rule).

    A rationale is required (accountability), exactly as for ``policy``/``takedown``;
    the reason gates the action but is not itself persisted, so a free-text note can
    never become a leak vector. Exactly one of ``--field`` / ``--payload`` is required;
    an unknown target is a clean error that names only the target, not any value.
    """
    if not args.reason or not args.reason.strip():
        raise LedgerError("a redaction requires a non-empty --reason")
    archive = _open_archive(Path(args.root))
    record = archive.get(args.id)
    now = args.now if args.now else now_iso()
    if args.field:
        if record.field_named(args.field) is None:
            raise LedgerError(f"record has no field named {args.field!r}")
        updated, event = redact_field(record, args.field, agent=args.actor, now=now)
        target = f"field {args.field!r}"
    else:
        if not any(p.filename == args.payload for p in record.payloads):
            raise LedgerError(f"record has no payload named {args.payload!r}")
        updated, event = redact_payload(record, args.payload, agent=args.actor, now=now)
        target = f"payload {args.payload!r}"
    _persist_record(archive, updated, event)
    print(f"redacted {target} from {args.id} by {args.actor}")
    return 0


def _cmd_verify_backup(args: argparse.Namespace) -> int:
    """``verify-backup`` — prove a backed-up archive restores intact (cron-friendly).

    An untested backup is a hope, not a backup (user research K1). Point this at a
    restored copy of the archive root (a directory holding ``store/`` and
    ``identity.vault``) and it re-validates the backup *in place*: it re-points the
    config at the backup location (the stored paths are the original box's), confirms
    the store and — when ``LEDGER_VAULT_KEY`` is set — the vault are readable without
    unsealing anything, then runs full RFC 8493 fixity over every bag. Exit ``0`` when
    every bag passes, non-zero otherwise, so a cron job can alarm on a bad backup.
    Only bag names and counts are printed (no-outing rule).
    """
    backup = Path(args.backup)
    result = verify_backup_location(backup)
    # A config/readiness failure yields no per-bag reports; report it as unreadable,
    # exactly as before (an empty *but readable* archive still verifies as PASS).
    if not result.ok and not result.bags:
        print(f"FAIL: backup is not readable ({result.reason})", file=sys.stderr)
        return 1

    for bag in result.bags:
        print(f"{'PASS' if bag.ok else 'FAIL'}\t{bag.name}\t({bag.checked} file(s) checked)")
    failures = result.failures
    summary = "PASS" if failures == 0 else "FAIL"
    print(f"{summary}: backup at {backup} — {len(result.bags)} bag(s) verified, {failures} failed")
    return 0 if failures == 0 else 1


def _proposal_store(archive: Archive) -> dualcontrol.ProposalStore:
    """The dual-control proposal store for ``archive`` (under ``logs/``)."""
    return dualcontrol.ProposalStore(archive.logs_dir / "proposals.json")


# Actions whose execution is *deferred* from the moment approval completes to a
# deliberate, separate ``--execute`` step. Lockdown and stand-up are irreversible or
# safety-critical enough that gathering approvals must not, by itself, fire them:
# assembling the authority and pulling the trigger are kept as two conscious acts.
_DEFERRED_ACTIONS: frozenset[str] = frozenset({"lockdown", "stand-up"})


def _deferred_ready_hint(root: str, proposal: dualcontrol.ActionProposal) -> str:
    """The line printed when a deferred proposal becomes approved (tells the operator
    the separate, deliberate command that actually performs it)."""
    return (
        f"proposal {proposal.proposal_id} ({proposal.action}) is APPROVED — "
        f"run the deliberate step: ledger {proposal.action} --root {root} "
        "--actor <steward> --execute"
    )


def _ready_deferred_proposal(archive: Archive, action: str) -> dualcontrol.ActionProposal:
    """The oldest open, approved proposal for ``action``; error if none is ready.

    The gate behind ``lockdown --execute`` / ``stand-up --execute``: the destructive
    or safety-critical step runs only when a dual-control proposal for it has reached
    the archive's approval threshold (accountability — no one steward acts alone)."""
    store = _proposal_store(archive)
    threshold = archive.config.dual_control_threshold
    for proposal in store.open_proposals():
        if proposal.action == action and proposal.is_ready(threshold):
            return proposal
    raise LedgerError(
        f"{action} --execute requires an APPROVED dual-control proposal; run "
        f"`ledger propose --action {action} --id <target> --actor <steward> --reason ...` "
        "and gather approvals with `ledger approve`"
    )


def _perform_takedown(
    archive: Archive, record_id: str, *, actor: str, reason: str, now: str
) -> str:
    """Record and execute a takedown; return a no-outing-safe summary line.

    The accountable decision is recorded and durably persisted FIRST (its audit
    trail of *why* must outlive the data), then every stored copy is removed and the
    contributor identity revoked through the one shared removal effect
    (:meth:`Archive.remove_all_copies`). Only the record id and counts appear in the
    summary (no-outing rule). Factored so both the direct path and an approved
    dual-control proposal execute the identical effect, which is itself shared with
    the in-UI steward console via :func:`ledger.moderate.execute_takedown`.
    """
    action, removed, revoked, had_identity = execute_takedown(
        archive, record_id, actor=actor, reason=reason, now=now
    )
    if had_identity and not revoked:  # pragma: no cover - vault failure is rare
        print(
            "warning: could not revoke identity from the vault; "
            "revoke it manually to complete the takedown",
            file=sys.stderr,
        )

    suffix = "; identity revoked" if revoked else ""
    return f"record {record_id} taken down by {action.actor}; {removed} copy(ies) removed{suffix}"


def _execute_proposal(
    archive: Archive, proposal: dualcontrol.ActionProposal, *, actor: str, now: str
) -> str:
    """Perform an approved proposal's action; return a no-outing-safe summary.

    ``takedown`` and ``publish`` execute their concrete effect. ``unseal`` records
    the *authorization* only: dual-control gates the decision, but the CLI never
    prints a contributor identity — retrieval stays the audited ``identity_unseal``
    grant path (no-outing rule)."""
    if proposal.action == "takedown":
        return _perform_takedown(
            archive, proposal.target, actor=actor, reason=proposal.reason, now=now
        )
    if proposal.action == "publish":
        record = archive.get(proposal.target)
        updated, event, _action = change_consent(
            record, AccessPolicy.PUBLIC, actor=actor, reason=proposal.reason, now=now
        )
        archive.apply_update(updated, event)
        return f"record {proposal.target} published by {actor}"
    if proposal.action == "unseal":
        return (
            f"identity-unseal for {proposal.target} authorized by "
            f"{proposal.approved_count()} steward(s) — retrieve via an identity_unseal "
            "grant; the CLI never prints an identity"
        )
    raise LedgerError(f"unknown proposal action: {proposal.action}")


def _cmd_takedown(args: argparse.Namespace) -> int:
    """``takedown`` — record an accountable takedown and remove stored copies.

    Under dual-control (``config.dual_control_threshold`` > 1) this *proposes* the
    takedown instead of executing it, so no single steward can erase a record alone;
    it runs only once enough distinct stewards approve (``ledger approve``). At the
    default threshold of 1 it executes immediately, exactly as before."""
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    threshold = archive.config.dual_control_threshold
    if threshold > 1:
        prop = _proposal_store(archive).add(
            dualcontrol.ActionProposal(
                action="takedown",
                target=args.id,
                reason=args.reason,
                proposer=args.actor,
                created_at=now,
            )
        )
        print(
            f"takedown PROPOSED for {args.id} (proposal {prop.proposal_id}); "
            f"needs {threshold} steward approvals ({prop.approved_count()}/{threshold}). "
            f"Another steward runs: ledger approve --root {args.root} "
            f"--id {prop.proposal_id} --actor <steward-id>"
        )
        return 0
    print(_perform_takedown(archive, args.id, actor=args.actor, reason=args.reason, now=now))
    return 0


# Actions the generic ``propose``/``approve`` path can *execute* (see
# :func:`_execute_proposal`). ``attest`` is deliberately excluded: it has its own
# ``ledger attest`` flow with a fixed 2-of-N quorum and a separate store, so it is
# never filed through the general dual-control path where it could not be executed.
_PROPOSABLE_ACTIONS: frozenset[str] = dualcontrol.ACTIONS - {"attest"}


def _cmd_propose(args: argparse.Namespace) -> int:
    """``propose`` — propose a high-stakes action for dual-control approval."""
    if args.action not in _PROPOSABLE_ACTIONS:
        raise LedgerError(
            f"unknown action {args.action!r}; expected one of {sorted(_PROPOSABLE_ACTIONS)}"
        )
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    store = _proposal_store(archive)
    prop = store.add(
        dualcontrol.ActionProposal(
            action=args.action,
            target=args.id,
            reason=args.reason,
            proposer=args.actor,
            created_at=now,
        )
    )
    threshold = archive.config.dual_control_threshold
    print(
        f"proposed {args.action} for {args.id} (proposal {prop.proposal_id}); "
        f"{prop.approved_count()}/{threshold} approval(s)"
    )
    if prop.is_ready(threshold):
        if prop.action in _DEFERRED_ACTIONS:
            print(_deferred_ready_hint(args.root, prop))
        else:
            print(_execute_proposal(archive, prop, actor=args.actor, now=now))
            store.mark(prop.proposal_id, "executed")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    """``approve`` — approve a pending proposal; execute it once the threshold is met."""
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    store = _proposal_store(archive)
    prop = store.approve(args.id, args.actor)
    threshold = archive.config.dual_control_threshold
    print(f"approved proposal {prop.proposal_id} ({prop.approved_count()}/{threshold})")
    if prop.is_ready(threshold):
        if prop.action in _DEFERRED_ACTIONS:
            print(_deferred_ready_hint(args.root, prop))
        else:
            print(_execute_proposal(archive, prop, actor=args.actor, now=now))
            store.mark(prop.proposal_id, "executed")
    return 0


def _cmd_lockdown(args: argparse.Namespace) -> int:
    """``lockdown`` — enter the duress posture (dry-run by default).

    Without ``--execute`` this is a **dry run**: it prints the ordered steps a
    lockdown would take (stop non-PUBLIC disclosure, and — only if configured and only
    after off-box replicas verify — shred the local vault) and changes nothing. With
    ``--execute`` it performs the lockdown, but only once a dual-control ``lockdown``
    proposal has been approved (``ledger propose``/``approve``), so no single steward
    can trigger a duress shred alone (accountability, fail-safe)."""
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    if not args.execute:
        for step in plan_lockdown(archive):
            print(step)
        print(
            "dry run — nothing changed. Propose+approve a 'lockdown' action, "
            "then re-run with --execute."
        )
        return 0
    proposal = _ready_deferred_proposal(archive, "lockdown")
    result = execute_lockdown(archive, actor=args.actor, now=now)
    _proposal_store(archive).mark(proposal.proposal_id, "executed")
    print(result.summary())
    for step in result.steps:
        print(f"  - {step}")
    print(result.runbook)
    return 0


def _cmd_stand_up(args: argparse.Namespace) -> int:
    """``stand-up`` — the inverse of lockdown: lift the freeze and restore (dry-run default).

    Without ``--execute`` it describes what standing the archive back up would do and
    reports whether it is currently locked down, changing nothing. With ``--execute``
    it verifies an off-box replica, restores the local vault from it if the vault was
    shredded, removes the lockdown flag, and resumes non-PUBLIC disclosure — but only
    once a dual-control ``stand-up`` proposal has been approved (accountability)."""
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    if not args.execute:
        print(
            "stand-up DRY-RUN — would verify an off-box replica, restore the vault if it "
            "was shredded, remove lockdown.flag, and resume non-PUBLIC disclosure."
        )
        print(f"currently locked down: {is_locked_down(archive)}")
        print(
            "dry run — nothing changed. Propose+approve a 'stand-up' action, "
            "then re-run with --execute."
        )
        return 0
    proposal = _ready_deferred_proposal(archive, "stand-up")
    result = execute_stand_up(archive, actor=args.actor, now=now)
    _proposal_store(archive).mark(proposal.proposal_id, "executed")
    print(result.summary())
    for step in result.steps:
        print(f"  - {step}")
    print(result.runbook)
    return 0


def _cmd_proposals(args: argparse.Namespace) -> int:
    """``proposals`` — list open dual-control proposals awaiting approval."""
    archive = _open_archive(Path(args.root))
    threshold = archive.config.dual_control_threshold
    open_props = _proposal_store(archive).open_proposals()
    for p in open_props:
        print(f"{p.proposal_id}\t{p.action}\t{p.target}\t{p.approved_count()}/{threshold}")
    print(f"({len(open_props)} open proposal(s); threshold {threshold})")
    return 0


def _attest_store(archive: Archive) -> attest.AttestStore:
    """The condition-attestation store for ``archive`` (under ``logs/``)."""
    return attest.AttestStore(archive.logs_dir)


def _cmd_attest_propose(args: argparse.Namespace) -> int:
    """``attest propose`` — propose that a SEALED_CONDITIONAL condition has been met.

    Validates the condition against the archive's controlled vocabulary
    (``config.conditions``) so a typo can never invent an ungoverned condition, then
    files a 2-of-N proposal: a *second, distinct* steward must ``attest approve`` it
    before it opens anything. One steward alone changes nothing (no one may declare a
    contributor dead by themselves)."""
    archive = _open_archive(Path(args.root))
    if args.condition not in archive.config.conditions:
        raise LedgerError(
            f"unknown condition {args.condition!r}; expected one of "
            f"{sorted(archive.config.conditions)} (edit config.conditions to add one)"
        )
    now = args.now if args.now else now_iso()
    prop = _attest_store(archive).propose(
        args.condition, args.actor, reason=args.reason or "", now=now
    )
    print(
        f"attestation PROPOSED for {args.condition!r} (proposal {prop.proposal_id}); "
        f"needs {attest.ATTEST_THRESHOLD} distinct stewards "
        f"({prop.approved_count()}/{attest.ATTEST_THRESHOLD}). "
        f"Another steward runs: ledger attest approve --root {args.root} "
        f"--id {prop.proposal_id} --actor <steward-id>"
    )
    return 0


def _cmd_attest_approve(args: argparse.Namespace) -> int:
    """``attest approve`` — approve a pending attestation; record it at the quorum.

    Counts only *distinct* stewards, so one steward approving twice never reaches the
    2-of-N quorum. On the approval that reaches quorum the condition is written into
    the durable attested-conditions set and a PREMIS ``POLICY_CHANGE`` event recorded,
    and every ``SEALED_CONDITIONAL`` field waiting on that condition opens on the next
    read."""
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    prop, attested_now = _attest_store(archive).approve(args.id, args.actor, now=now)
    print(
        f"approved attestation {prop.proposal_id} "
        f"({prop.approved_count()}/{attest.ATTEST_THRESHOLD})"
    )
    if attested_now:
        print(f"condition {prop.target!r} is now attested-met; fields sealed on it now disclose")
    return 0


def _cmd_attest_list(args: argparse.Namespace) -> int:
    """``attest list`` — show open attestation proposals and attested conditions."""
    store = _attest_store(_open_archive(Path(args.root)))
    open_props = store.open_proposals()
    for p in open_props:
        print(f"{p.proposal_id}\t{p.target}\t{p.approved_count()}/{attest.ATTEST_THRESHOLD}")
    met = sorted(store.attested())
    print(f"({len(open_props)} open proposal(s); attested-met: {', '.join(met) or 'none'})")
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


def _cmd_handoff(args: argparse.Namespace) -> int:
    """``handoff`` — produce a continuity hand-off manifest for a folding group (EX1).

    Builds a no-outing-safe :class:`~ledger.succession.HandoffManifest`: it
    re-verifies every bag's fixity, inventories the records by opaque id, records
    where the bytes and the *encrypted* vault live, and embeds a plain-language
    runbook a designated successor can follow to stand the archive back up and prove
    it arrived intact. The manifest carries no contributor identity, no sealed value,
    and never the vault key (which must travel out-of-band).

    Writes the JSON manifest to ``--out`` when given (else prints it to stdout), and
    a short, safe summary to stdout. Exits non-zero if any bag failed fixity, so a
    group never hands off a corrupt archive believing it is whole.
    """
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    manifest = succession.build_handoff(
        archive, now=now, successor=args.successor, attest_steward=args.attest_steward
    )
    if args.attest_steward:
        print(
            "filed a 'group-dissolved' attestation proposal; a second steward must "
            "'ledger attest approve' it before any conditional seal opens",
            file=sys.stderr,
        )
    manifest_json = manifest.to_json()
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(manifest_json + "\n", encoding="utf-8")
        print(f"wrote hand-off manifest for {manifest.total_records} record(s) to {out_path}")
    else:
        print(manifest_json)
    status = "all bags verified" if manifest.all_fixity_ok else "FIXITY FAILURES PRESENT"
    print(
        f"hand-off: {manifest.total_records} record(s); {status}; "
        f"vault {'present' if manifest.vault_present else 'absent'} "
        "(copy its key out-of-band, never in the manifest)",
        file=sys.stderr,
    )
    return 0 if manifest.all_fixity_ok else 1


def _cmd_export_drive(args: argparse.Namespace) -> int:
    """``export-drive`` — build a self-verifying offline courier package (EXP-08).

    Disclosure-filtered like every other read path: ``--as`` picks the viewer
    (default: anonymous/PUBLIC, the narrowest — least privilege), and only what
    that grant may see is re-bagged onto the package. Exits non-zero if any
    freshly written bag fails its own validation, so a bad package is never
    reported as ready to hand to a courier.
    """
    archive = _open_archive(Path(args.root))
    grant = _grant_for(args.as_subject)
    now = args.now if args.now else now_iso()
    result = build_export_drive(
        archive,
        Path(args.out),
        grant=grant,
        archive_name=archive.config.archive_name,
        base_url=args.base_url or "",
        now=now,
    )
    status = "all bags verified" if result.all_bags_valid else "BAG VALIDATION FAILED"
    print(
        f"export-drive: {result.records_packaged} record(s), {result.files_packaged} file(s) "
        f"packaged to {result.out_dir} for viewer {grant.subject!r}; {status}"
    )
    return 0 if result.all_bags_valid else 1


def _cmd_print_edition(args: argparse.Namespace) -> int:
    """``print-edition`` — render an accessible, zine-style HTML booklet (EXP-08).

    PUBLIC-only by construction: always uses the anonymous grant, regardless of
    who runs the command, because a printed page cannot later be redacted. Each
    entry carries a visible SHA-256 fixity line plus, when the optional
    ``segno`` package is installed (``pip install ledger-archive[print]``), a
    scannable QR code encoding the same string. See the module docstring in
    :mod:`ledger.print_edition` for why this renders HTML, not a bespoke PDF.
    """
    archive = _open_archive(Path(args.root))
    now = args.now if args.now else now_iso()
    record_ids = args.id if args.id else None
    result = build_print_edition(
        archive,
        Path(args.out),
        record_ids=record_ids,
        archive_name=archive.config.archive_name,
        base_url=args.base_url or "",
        lang=args.lang,
        now=now,
    )
    qr_note = (
        "with QR codes" if result.qr_codes_rendered else "text-only fixity (segno not installed)"
    )
    print(f"print-edition: {result.records_included} record(s) -> {result.out_path} ({qr_note})")
    return 0


def _cmd_session_ingest(args: argparse.Namespace) -> int:
    """``session ingest`` — apply an oral-history session manifest and ingest it.

    EXP-09: reads a session-manifest JSON file (see
    ``docs/oral-history/session-manifest-format.md``), validates that every
    disclosing segment carries a spoken-consent timestamp, maps each segment onto
    its own :class:`~ledger.models.Field` (and, for a segment naming a
    ``payload_filename``, a pre-declared payload policy), and runs the result
    through the one ingest path — exactly like ``ingest``, but session-shaped.
    """
    archive = _open_archive(Path(args.root))
    manifest = parse_session_manifest(Path(args.manifest).read_text(encoding="utf-8"))

    record = Record(
        title=args.title,
        default_policy=AccessPolicy.SEALED_UNTIL,
        dublin_core=DublinCore(title=[args.title], publisher=[archive.config.archive_name]),
        content_warnings=list(args.cw or []),
    )
    record = apply_session_manifest(record, manifest)

    payload: dict[str, Path] = {}
    for fname, path_str in _parse_pairs(args.file or []):
        payload[fname] = Path(path_str)

    identity: ContributorIdentity | None = None
    if args.narrator_name or args.narrator_contact:
        identity = ContributorIdentity(
            name=args.narrator_name or "",
            contact=args.narrator_contact or "",
        )

    now = args.now if args.now else now_iso()
    aip = archive.ingest(payload, record, identity=identity, agent=args.actor, now=now)

    print(f"record_id: {record.record_id}")
    print(f"bag: {aip.bag.path}")
    print(f"segments: {len(manifest.segments)}")
    if record.identity_ref is not None:
        # Print ONLY the opaque token; never the narrator's name or contact.
        print(f"identity_ref: {record.identity_ref}")
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
    p_ingest.add_argument(
        "--transcript",
        action="append",
        metavar="filename=text",
        help="a transcript/caption for an audio or video payload (accessibility)",
    )
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
        "--revocations",
        help="path to a revoked-subjects JSON file (default: revocations.json beside --grants)",
    )
    p_serve.add_argument(
        "--allow-contributions",
        action="store_true",
        help="enable the /contribute submission form (requires LEDGER_VAULT_KEY)",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_grant = sub.add_parser("grant", help="mint, revoke, and list capability grant tokens")
    grant_sub = p_grant.add_subparsers(dest="grant_command", required=True, metavar="SUBCOMMAND")
    p_grant_issue = grant_sub.add_parser(
        "issue", help="mint an authenticated token (secret via LEDGER_GRANT_SECRET)"
    )
    p_grant_issue.add_argument("--subject", required=True, help="subject the token authenticates")
    p_grant_issue.add_argument("--expires-at", help="ISO-8601 expiry (default: never expires)")
    p_grant_issue.set_defaults(func=_cmd_grant_issue)
    p_grant_revoke = grant_sub.add_parser("revoke", help="add a subject to the revocation list")
    p_grant_revoke.add_argument("--subject", required=True, help="subject to revoke")
    p_grant_revoke.add_argument(
        "--revocations", required=True, help="path to the revoked-subjects JSON file"
    )
    p_grant_revoke.set_defaults(func=_cmd_grant_revoke)
    p_grant_list = grant_sub.add_parser(
        "list", help="list provisioned subjects with their revoked flag"
    )
    p_grant_list.add_argument("--grants", help="path to the pre-provisioned grants JSON file")
    p_grant_list.add_argument("--revocations", help="path to the revoked-subjects JSON file")
    p_grant_list.set_defaults(func=_cmd_grant_list)

    p_audit = sub.add_parser("audit", help="validate every bag's fixity")
    p_audit.add_argument("--root", required=True)
    p_audit.set_defaults(func=_cmd_audit)

    p_verify_backup = sub.add_parser(
        "verify-backup", help="prove a restored backup is intact (cron-friendly)"
    )
    p_verify_backup.add_argument(
        "--backup", required=True, help="path to a restored archive root (holds store/ + vault)"
    )
    p_verify_backup.set_defaults(func=_cmd_verify_backup)

    p_policy = sub.add_parser("policy", help="record an accountable consent/policy change")
    p_policy.add_argument("--root", required=True)
    p_policy.add_argument("--id", required=True)
    p_policy.add_argument("--level", required=True, help="new default access level")
    p_policy.add_argument("--actor", required=True, help="steward id making the change")
    p_policy.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_policy.add_argument("--now", help="ISO-8601 timestamp")
    p_policy.set_defaults(func=_cmd_policy)

    p_seal = sub.add_parser(
        "seal", help="set the disclosure policy of a field, payload, or record default"
    )
    p_seal.add_argument("--root", required=True)
    p_seal.add_argument("--id", required=True)
    seal_target = p_seal.add_mutually_exclusive_group(required=True)
    seal_target.add_argument("--field", help="name of the field to set a policy on")
    seal_target.add_argument("--payload", help="filename of the payload to set a policy on")
    seal_target.add_argument(
        "--default", action="store_true", help="set the record's default policy"
    )
    p_seal.add_argument("--level", required=True, help="access level to set")
    p_seal.add_argument("--until", help="embargo date (ISO-8601); --field with sealed-until only")
    p_seal.add_argument("--condition", help="unseal condition name; --field only")
    p_seal.add_argument("--actor", required=True, help="steward id making the change")
    p_seal.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_seal.add_argument("--now", help="ISO-8601 timestamp")
    p_seal.set_defaults(func=_cmd_seal)

    p_redact = sub.add_parser("redact", help="apply a recorded redaction to a field or payload")
    p_redact.add_argument("--root", required=True)
    p_redact.add_argument("--id", required=True)
    redact_target = p_redact.add_mutually_exclusive_group(required=True)
    redact_target.add_argument("--field", help="name of the field to redact")
    redact_target.add_argument("--payload", help="filename of the payload to drop")
    p_redact.add_argument("--actor", required=True, help="steward id making the change")
    p_redact.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_redact.add_argument("--now", help="ISO-8601 timestamp")
    p_redact.set_defaults(func=_cmd_redact)

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

    p_propose = sub.add_parser("propose", help="propose a high-stakes action (dual-control)")
    p_propose.add_argument("--root", required=True)
    p_propose.add_argument(
        "--action",
        required=True,
        choices=sorted(_PROPOSABLE_ACTIONS),
        help="action to propose",
    )
    p_propose.add_argument(
        "--id", required=True, help="target record id (or identity ref for unseal)"
    )
    p_propose.add_argument("--actor", required=True, help="proposing steward id")
    p_propose.add_argument("--reason", required=True, help="rationale (required, auditable)")
    p_propose.add_argument("--now", help="ISO-8601 timestamp")
    p_propose.set_defaults(func=_cmd_propose)

    p_approve = sub.add_parser("approve", help="approve a pending dual-control proposal")
    p_approve.add_argument("--root", required=True)
    p_approve.add_argument("--id", required=True, help="proposal id")
    p_approve.add_argument("--actor", required=True, help="approving steward id")
    p_approve.add_argument("--now", help="ISO-8601 timestamp")
    p_approve.set_defaults(func=_cmd_approve)

    p_proposals = sub.add_parser("proposals", help="list open dual-control proposals")
    p_proposals.add_argument("--root", required=True)
    p_proposals.set_defaults(func=_cmd_proposals)

    p_attest = sub.add_parser(
        "attest", help="attest a SEALED_CONDITIONAL condition (2-of-N stewards)"
    )
    attest_sub = p_attest.add_subparsers(dest="attest_command", required=True, metavar="SUBCOMMAND")
    p_att_propose = attest_sub.add_parser("propose", help="propose that a condition has been met")
    p_att_propose.add_argument("--root", required=True)
    p_att_propose.add_argument("condition", help="condition name (from config.conditions)")
    p_att_propose.add_argument("--actor", required=True, help="proposing steward id")
    p_att_propose.add_argument("--reason", help="rationale (auditable; not persisted verbatim)")
    p_att_propose.add_argument("--now", help="ISO-8601 timestamp")
    p_att_propose.set_defaults(func=_cmd_attest_propose)

    p_att_approve = attest_sub.add_parser(
        "approve", help="approve a pending attestation; records it at 2-of-N"
    )
    p_att_approve.add_argument("--root", required=True)
    p_att_approve.add_argument("--id", required=True, help="attestation proposal id")
    p_att_approve.add_argument("--actor", required=True, help="approving steward id")
    p_att_approve.add_argument("--now", help="ISO-8601 timestamp")
    p_att_approve.set_defaults(func=_cmd_attest_approve)

    p_att_list = attest_sub.add_parser(
        "list", help="list open attestations and attested-met conditions"
    )
    p_att_list.add_argument("--root", required=True)
    p_att_list.set_defaults(func=_cmd_attest_list)

    p_lockdown = sub.add_parser(
        "lockdown",
        help="enter the duress posture (dry-run default; --execute needs an approved proposal)",
    )
    p_lockdown.add_argument("--root", required=True)
    p_lockdown.add_argument("--actor", required=True, help="steward id triggering the lockdown")
    p_lockdown.add_argument(
        "--execute",
        action="store_true",
        help="perform it (requires an approved dual-control 'lockdown' proposal)",
    )
    p_lockdown.add_argument("--now", help="ISO-8601 timestamp")
    p_lockdown.set_defaults(func=_cmd_lockdown)

    p_standup = sub.add_parser(
        "stand-up",
        help="lift lockdown and restore (dry-run default; --execute needs an approved proposal)",
    )
    p_standup.add_argument("--root", required=True)
    p_standup.add_argument("--actor", required=True, help="steward id standing the archive back up")
    p_standup.add_argument(
        "--execute",
        action="store_true",
        help="perform it (requires an approved dual-control 'stand-up' proposal)",
    )
    p_standup.add_argument("--now", help="ISO-8601 timestamp")
    p_standup.set_defaults(func=_cmd_stand_up)

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

    p_handoff = sub.add_parser(
        "handoff", help="produce a continuity hand-off manifest (group succession)"
    )
    p_handoff.add_argument("--root", required=True)
    p_handoff.add_argument("--successor", help="name of the collective/person taking over")
    p_handoff.add_argument(
        "--attest-steward",
        help="steward id filing a 'group-dissolved' attestation proposal at hand-off "
        "(still needs a second steward's approval before any seal opens)",
    )
    p_handoff.add_argument("--out", help="write the JSON manifest here (default: stdout)")
    p_handoff.add_argument("--now", help="ISO-8601 timestamp for a reproducible manifest")
    p_handoff.set_defaults(func=_cmd_handoff)

    p_export_drive = sub.add_parser(
        "export-drive", help="build a self-verifying offline courier package (sneakernet)"
    )
    p_export_drive.add_argument("--root", required=True)
    p_export_drive.add_argument("--out", required=True, help="output directory (must not exist)")
    p_export_drive.add_argument(
        "--as", dest="as_subject", help="viewer subject to disclose for (default: anonymous)"
    )
    p_export_drive.add_argument("--base-url", help="public base URL for the manifest.csv links")
    p_export_drive.add_argument("--now", help="ISO-8601 timestamp")
    p_export_drive.set_defaults(func=_cmd_export_drive)

    p_print_edition = sub.add_parser(
        "print-edition", help="render a zine-style accessible HTML booklet of PUBLIC records"
    )
    p_print_edition.add_argument("--root", required=True)
    p_print_edition.add_argument("--out", required=True, help="output HTML file path")
    p_print_edition.add_argument(
        "--id", action="append", help="record id to include (repeatable; default: all PUBLIC)"
    )
    p_print_edition.add_argument("--base-url", help="public base URL for fixity verify strings")
    p_print_edition.add_argument("--lang", default="en", help="booklet language tag")
    p_print_edition.add_argument("--now", help="ISO-8601 timestamp")
    p_print_edition.set_defaults(func=_cmd_print_edition)

    p_session = sub.add_parser("session", help="oral-history session kit")
    session_sub = p_session.add_subparsers(
        dest="session_command", required=True, metavar="SUBCOMMAND"
    )
    p_session_ingest = session_sub.add_parser(
        "ingest", help="apply a session manifest (EXP-09) and ingest the result"
    )
    p_session_ingest.add_argument("--root", required=True)
    p_session_ingest.add_argument("--title", required=True)
    p_session_ingest.add_argument(
        "--manifest", required=True, help="path to a session-manifest JSON file"
    )
    p_session_ingest.add_argument(
        "--file",
        action="append",
        metavar="filename=path",
        help="bytes for a segment's payload_filename (repeatable)",
    )
    p_session_ingest.add_argument("--cw", action="append", metavar="WARNING")
    p_session_ingest.add_argument(
        "--narrator-name", help="sealed into the vault; never printed back"
    )
    p_session_ingest.add_argument("--narrator-contact", help="sealed into the vault")
    p_session_ingest.add_argument("--actor", default="ledger", help="ingest agent id")
    p_session_ingest.add_argument("--now", help="ISO-8601 timestamp for reproducible ingest")
    p_session_ingest.set_defaults(func=_cmd_session_ingest)

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
