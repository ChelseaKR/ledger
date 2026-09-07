"""Reversible disaster rehearsal: prove the recovery paths on this archive's shape.

``ledger checkup`` inspects a deployment and ``docs/BACKUP-RUNBOOK.md`` §4
describes a restore drill a steward runs by hand. Neither exercises the failures
the threat model is written for: a replica silently corrupted, a location that
disappears, a history quietly truncated, a stale copy reattaching after a
takedown. An untested recovery path is a hope, as the runbook says.

A drill copies the archive into a scratch directory, injects one named fault into
the copy, runs the archive's own recovery commands against it, and reports what
happened. The live archive is never written.

Two rules shape every scenario here, and both exist because a rehearsal that
cannot fail is worse than no rehearsal.

**The fault must be confirmed present before recovery is attempted.** A sabotage
that silently no-ops reads as a pass: the recovery "succeeds" because there was
nothing to recover from, and the report says the archive survived a fault it
never suffered. So each scenario re-reads the copy after injecting and asserts the
mutation is really there, and the outcome records that it did.

**The archive's own detector must see the fault before recovery is credited.** It
is not enough that the bytes changed; the check a steward relies on has to notice.
A scenario whose damage went undetected is a :data:`DrillOutcome.FAILED`, and the
report names the step, because a recovery path that only works when someone
already knows what broke is not a recovery path.

A scenario the archive's shape cannot exercise — ``lost-location`` on an archive
with one location — reports :data:`DrillOutcome.NOT_APPLICABLE` with the reason.
Never ``recovered``. This is the same three-state honesty
``ledger.checkup.CheckStatus`` already uses: "it worked" and "it was not tried"
are different facts and a reader must be able to tell them apart.

No-outing: reports carry scenario names, counts, hashes and command names. Never
a payload byte, a record title, or an identity.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ledger.bag import validate_bag
from ledger.config import Config, StorageLocation
from ledger.models import now_iso
from ledger.replicate import heal, verify_replicas
from ledger.tombstones import TombstoneStore

#: The agent recorded on any PREMIS event a drill's recovery writes into the
#: *scratch copy*. It never reaches the live archive, but it is spelled out so a
#: reader who finds one in a scratch tree knows immediately what made it.
DRILL_AGENT = "ledger-drill"

#: Bytes written over a payload file to simulate bit rot. Chosen to be a value a
#: real file is unlikely to already start with, so the mutation is detectable by
#: reading it back rather than assumed.
_ROT_BYTES = b"\x00\xffROT"


class DrillOutcome(StrEnum):
    """What one scenario ended in. Three states, and the third is load-bearing."""

    RECOVERED = "recovered"
    FAILED = "failed"
    #: The archive's shape cannot exercise this scenario. Not a pass, not a
    #: failure, and never rendered as either.
    NOT_APPLICABLE = "not-applicable"


class DrillError(Exception):
    """The drill could not be set up: a missing archive, or an unknown scenario."""


@dataclass(frozen=True)
class Injection:
    """The result of injecting one fault: what was done, and whether it landed."""

    description: str
    landed: bool
    bags_touched: int
    #: Why the injection did not land, when it did not. A scenario that cannot
    #: damage what it meant to damage says so rather than proceeding.
    reason: str = ""


@dataclass(frozen=True)
class ScenarioResult:
    """One rehearsal: the fault, whether it was seen, and whether recovery worked."""

    scenario: str
    outcome: DrillOutcome
    fault: str
    #: The injected mutation was confirmed present on disk before recovery ran.
    fault_landed: bool
    #: The archive's own check reported the damage. False with ``fault_landed``
    #: true is the most interesting failure a drill can produce: the recovery
    #: path exists but nothing would ever call it.
    fault_detected: bool
    bags_touched: int
    recovering_command: str
    detail: str
    #: Which step ended the scenario, for a failure. ``None`` on success.
    failing_step: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "outcome": self.outcome.value,
            "fault": self.fault,
            "fault_landed": self.fault_landed,
            "fault_detected": self.fault_detected,
            "bags_touched": self.bags_touched,
            "recovering_command": self.recovering_command,
            "detail": self.detail,
            "failing_step": self.failing_step,
        }


@dataclass
class DrillReport:
    """Every scenario's outcome, plus proof the live archive was not written."""

    generated_date: str
    archive_name: str
    results: tuple[ScenarioResult, ...]
    #: Digest of the live archive tree before the drill and after it. They must
    #: be equal; the report carries both so a reader is not asked to take it on
    #: trust, and :attr:`live_archive_untouched` is what the exit code reads.
    live_digest_before: str
    live_digest_after: str
    report_path: Path | None = field(default=None)

    @property
    def live_archive_untouched(self) -> bool:
        return self.live_digest_before == self.live_digest_after

    @property
    def exit_code(self) -> int:
        """``1`` when any scenario failed, or when the live archive moved.

        ``not-applicable`` does not fail the drill: an archive with one location
        genuinely cannot rehearse losing one, and turning that into a red would
        train stewards to ignore the command. It is reported, loudly, instead.
        """
        if not self.live_archive_untouched:
            return 1
        return 1 if any(r.outcome is DrillOutcome.FAILED for r in self.results) else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_date": self.generated_date,
            "archive_name": self.archive_name,
            "live_archive_untouched": self.live_archive_untouched,
            "live_digest_before": self.live_digest_before,
            "live_digest_after": self.live_digest_after,
            "scenarios": [r.to_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Recovery drill — {self.archive_name}",
            "",
            f"Run {self.generated_date}. Each row below is a deliberate fault injected "
            "into a scratch copy of this archive, followed by the archive's own "
            "recovery command run against the damaged copy.",
            "",
            "A scenario is only credited as recovered when the injected fault was "
            "confirmed present on disk **and** the archive's own check reported it. A "
            "fault that went undetected is a failure even if the archive ends up "
            "whole, because nothing would have called the recovery in real life.",
            "",
            "| Scenario | Fault | Landed | Detected | Bags | Recovered by | Outcome |",
            "|---|---|:-:|:-:|---:|---|---|",
        ]
        for result in self.results:
            lines.append(
                f"| `{result.scenario}` | {result.fault} | "
                f"{'yes' if result.fault_landed else 'no'} | "
                f"{'yes' if result.fault_detected else 'no'} | {result.bags_touched} | "
                f"`{result.recovering_command}` | **{result.outcome.value}** |"
            )
        lines += ["", "## What each scenario found", ""]
        for result in self.results:
            step = f" (stopped at: {result.failing_step})" if result.failing_step else ""
            lines.append(
                f"- **`{result.scenario}`** — {result.outcome.value}{step}. {result.detail}"
            )
        lines += [
            "",
            "## The live archive",
            "",
            f"Digest before: `{self.live_digest_before}`  ",
            f"Digest after:  `{self.live_digest_after}`  ",
            (
                "The drill wrote nothing to the live archive."
                if self.live_archive_untouched
                else "**The live archive changed during this drill. That is a defect in "
                "the drill itself; do not read the outcomes above as evidence.**"
            ),
            "",
            "Names, counts and digests only. No payload byte, record title or "
            "identity appears in this report.",
        ]
        return "\n".join(lines) + "\n"


# --- staging: a scratch copy the drill is allowed to break ---------------------------


def tree_digest(root: Path) -> str:
    """A stable digest of every file under ``root``: relative path plus content.

    Used to prove the live archive did not move. Paths are included so a deletion
    or a rename shows up, which a digest over concatenated contents alone would
    miss.
    """

    digest = hashlib.sha256()
    if not root.exists():
        return "absent"
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class StagedArchive:
    """A scratch copy of an archive, with its config rewritten to point at itself."""

    #: The staged equivalent of the CLI's ``--root``: the directory holding
    #: ``store/``, so a staged archive can be handed to the same helpers.
    root: Path
    store_root: Path
    config: Config
    locations: tuple[StorageLocation, ...]

    def bag_names(self) -> tuple[str, ...]:
        bags = self.store_root / "bags"
        if not bags.is_dir():
            return ()
        return tuple(sorted(p.name for p in bags.iterdir() if p.is_dir()))


CONFIG_RELATIVE = Path("store") / "config.json"


def _remap(path: Path, live_root: Path, staged_root: Path) -> Path | None:
    """``path``'s equivalent inside the staged tree, or ``None`` if it is outside.

    A location under the archive root -- the default ``primary``, which points at
    ``store/bags`` -- must keep pointing at the *staged store's* bags rather than
    at a second copy of them. Otherwise a heal would repair a copy nobody reads and
    the scenario would credit a recovery that never touched the authoritative bags.
    """

    try:
        return staged_root / path.resolve().relative_to(live_root.resolve())
    except ValueError:
        return None


def stage(live_root: Path, workdir: Path) -> StagedArchive:
    """Copy ``live_root`` and every out-of-tree location into ``workdir``.

    The copied ``store/config.json`` is rewritten so every path points inside
    ``workdir``. Nothing in the staged archive can reach back to the real one,
    which is what makes it safe to break: a scenario that deletes a location
    deletes a copy of one.
    """

    config_path = live_root / CONFIG_RELATIVE
    if not config_path.is_file():
        raise DrillError(f"no ledger archive at {live_root} (no {CONFIG_RELATIVE})")
    config = Config.load(config_path)

    staged_root = workdir / "archive"
    shutil.copytree(live_root, staged_root)
    store_root = _remap(Path(config.store_root), live_root, staged_root) or (staged_root / "store")

    staged_locations: list[StorageLocation] = []
    for index, location in enumerate(config.locations):
        source = Path(location.path).expanduser()
        inside = _remap(source, live_root, staged_root)
        if inside is not None:
            dest = inside
        else:
            dest = workdir / "locations" / f"{index:02d}-{_safe(location.name)}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.copytree(source, dest)
            else:
                # A location configured but absent on disk is staged as an empty
                # directory rather than skipped: the drill rehearses the archive as
                # configured, and an unreachable mirror is itself a finding the
                # scenarios below will surface.
                dest.mkdir(parents=True, exist_ok=True)
        staged_locations.append(
            StorageLocation(name=location.name, path=str(dest), kind=location.kind)
        )

    vault = _remap(Path(config.vault_path), live_root, staged_root)
    config.store_root = str(store_root)
    config.vault_path = str(vault or (staged_root / Path(config.vault_path).name))
    config.locations = list(staged_locations)
    config.save(store_root / "config.json")
    return StagedArchive(
        root=staged_root,
        store_root=store_root,
        config=config,
        locations=tuple(staged_locations),
    )


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "location"


# --- the scenario registry -----------------------------------------------------------


def _default_source_check(staged: StagedArchive) -> bool:
    """The final state almost every scenario means: the source bag still validates.

    Named (rather than inlined as a lambda default) so the exception is legible:
    :func:`_stale_replica_scenario` is the one scenario that overrides it, and it
    overrides it with the opposite claim.
    """

    return _source_bag_still_validates(staged)


@dataclass(frozen=True)
class Scenario:
    """One rehearsal, as five functions the runner calls in a fixed order.

    Splitting it this way is what lets the runner enforce the discipline in the
    module docstring rather than trusting each scenario to remember it: the runner
    checks applicability, injects, insists the injection landed, insists the
    archive's own detector saw it, and only then runs the recovery.
    """

    name: str
    fault: str
    recovering_command: str
    #: Why this archive cannot rehearse the scenario, or ``None`` if it can.
    applicability: Callable[[StagedArchive], str | None]
    inject: Callable[[StagedArchive], Injection]
    #: Does the archive's own check report the damage? Never inspects the fault
    #: directly: it must go through the same code a steward would run.
    detect: Callable[[StagedArchive], bool]
    recover: Callable[[StagedArchive], None]
    #: Is the archive whole again?
    verify: Callable[[StagedArchive], bool]
    #: What the authoritative store under ``bags/`` must look like when the
    #: scenario ends. For every damage scenario that is "the source bag still
    #: validates" — a heal that repaired the mirrors by damaging the source is not
    #: a recovery. ``stale-replica`` is the inverse: there the recovery's job is to
    #: **refuse** to bring a bag back, so the source must be *gone*. Making it a
    #: field rather than a fixed call in the runner is what lets a scenario say
    #: which of those two it means, instead of the runner assuming.
    source_check: Callable[[StagedArchive], bool] = field(default=_default_source_check)


def _mirrors(staged: StagedArchive) -> list[StorageLocation]:
    return [loc for loc in staged.locations if loc.kind == "mirror"]


def _first_bag(staged: StagedArchive) -> str | None:
    names = staged.bag_names()
    return names[0] if names else None


def _replica_dir(location: StorageLocation, bag_name: str) -> Path:
    return Path(location.path) / bag_name


def _needs_a_bag_and_a_mirror(staged: StagedArchive) -> str | None:
    if _first_bag(staged) is None:
        return "the archive holds no bags, so there is nothing to damage or recover"
    if not _mirrors(staged):
        return "the archive has no mirror location, so a replica cannot be damaged"
    return None


def _replicas_report_a_problem(staged: StagedArchive) -> bool:
    """The archive's own replica verification sees something wrong.

    Deliberately routed through :func:`ledger.replicate.verify_replicas` rather
    than re-reading the file the scenario just broke: the question is whether the
    check a steward runs would notice, not whether the bytes changed.
    """

    bag = _first_bag(staged)
    if bag is None:
        return False
    statuses = verify_replicas(bag, list(staged.locations))
    return any(not status.ok for status in statuses)


def _tombstone_store(staged: StagedArchive) -> TombstoneStore:
    """The staged archive's own takedown store, at the path ``ledger heal`` reads.

    ``Archive.logs_dir`` is ``store_root / "logs"``; the drill does not open an
    :class:`~ledger.ingest.Archive` (that would decrypt a vault it has no business
    touching), so the one path is spelled here.
    """

    return TombstoneStore(staged.store_root / "logs")


def _heal_the_first_bag(staged: StagedArchive) -> None:
    """Run ``ledger heal`` against the staged copy — tombstones and all.

    The store is passed for the same reason :func:`ledger.cli._cmd_heal` passes it:
    without it this is not the command the report names. ``heal`` applies pending
    takedowns before copying anything, and refuses to resurrect a tombstoned bag.
    On an archive with no takedowns that sweep is a no-op, so the three damage
    scenarios behave exactly as before; on ``stale-replica`` it is the whole point.
    """

    bag = _first_bag(staged)
    if bag is None:  # pragma: no cover - applicability already refused this
        return
    heal(
        bag,
        list(staged.locations),
        agent=DRILL_AGENT,
        now=now_iso(),
        tombstones=_tombstone_store(staged),
    )


def _every_replica_is_whole(staged: StagedArchive) -> bool:
    bag = _first_bag(staged)
    if bag is None:  # pragma: no cover - applicability already refused this
        return False
    statuses = verify_replicas(bag, list(staged.locations))
    return bool(statuses) and all(status.ok for status in statuses)


def _inject_bit_rot(staged: StagedArchive) -> Injection:
    """Overwrite the head of one payload file in one mirror."""

    bag = _first_bag(staged)
    mirror = _mirrors(staged)[0]
    replica = _replica_dir(mirror, bag or "")
    payloads = sorted(p for p in (replica / "data").rglob("*") if p.is_file())
    if not payloads:
        return Injection(
            description="no payload file in the mirrored bag",
            landed=False,
            bags_touched=0,
            reason="the mirrored bag carries no data/ file to corrupt",
        )
    target = payloads[0]
    original = target.read_bytes()
    target.write_bytes(_ROT_BYTES + original[len(_ROT_BYTES) :])
    landed = target.read_bytes() != original
    return Injection(
        description=f"{len(_ROT_BYTES)} bytes overwritten in one payload file of mirror "
        f"{mirror.name!r}",
        landed=landed,
        bags_touched=1,
        reason="" if landed else "the payload file read back unchanged after the write",
    )


def _inject_lost_location(staged: StagedArchive) -> Injection:
    """Remove one mirror's copy of the bag entirely."""

    bag = _first_bag(staged)
    mirror = _mirrors(staged)[0]
    replica = _replica_dir(mirror, bag or "")
    if not replica.exists():
        return Injection(
            description=f"mirror {mirror.name!r} already holds no copy of the bag",
            landed=False,
            bags_touched=0,
            reason="there was no replica to remove; nothing was rehearsed",
        )
    shutil.rmtree(replica)
    return Injection(
        description=f"the whole replica removed from mirror {mirror.name!r}",
        landed=not replica.exists(),
        bags_touched=1,
        reason="" if not replica.exists() else "the replica directory still exists",
    )


def _inject_truncated_log(staged: StagedArchive) -> Injection:
    """Cut the PREMIS chain in one mirror's copy."""

    bag = _first_bag(staged)
    mirror = _mirrors(staged)[0]
    premis = _replica_dir(mirror, bag or "") / "premis.json"
    if not premis.is_file():
        return Injection(
            description="the mirrored bag carries no premis.json",
            landed=False,
            bags_touched=0,
            reason="there is no history to truncate in this replica",
        )
    original = premis.read_bytes()
    premis.write_bytes(original[: max(1, len(original) // 2)])
    landed = premis.read_bytes() != original
    return Injection(
        description=f"premis.json truncated to half its length in mirror {mirror.name!r}",
        landed=landed,
        bags_touched=1,
        reason="" if landed else "premis.json read back unchanged after the truncation",
    )


def _inject_stale_replica(staged: StagedArchive) -> Injection:
    """Take a record down while one mirror is "offline", so its copy survives there.

    This is the shape the threat model worries about and the other three scenarios
    do not reach: the removal was lawful and it was applied everywhere the archive
    could reach, and then the box that was unplugged comes back carrying the
    record anyway.

    The injection is therefore not damage to a bag. It is a *state*: a tombstone
    in the archive's own store, no copy at any location that was reachable, and a
    surviving copy at the mirror that was not. All three are read back off disk
    before this claims to have landed.
    """

    bag = _first_bag(staged)
    mirror = _mirrors(staged)[0]
    replica = _replica_dir(mirror, bag or "")
    if not replica.exists():
        return Injection(
            description=f"mirror {mirror.name!r} holds no copy of the bag",
            landed=False,
            bags_touched=0,
            reason="there is no copy for a takedown to leave behind, so nothing was rehearsed",
        )

    store = _tombstone_store(staged)
    if store.is_tombstoned(bag or ""):
        # The drill would otherwise credit itself with a takedown this archive had
        # already recorded, and rehearse a fault it did not cause.
        return Injection(
            description=f"record {bag!r} was already tombstoned in this archive",
            landed=False,
            bags_touched=0,
            reason=(
                "the takedown this scenario rehearses was already on record before the "
                "drill ran, so the drill did not cause the fault it would credit"
            ),
        )

    store.add(bag or "", now_iso())
    # What a takedown does at every location that *was* reachable. The mirror is
    # the one that was not, so its copy is deliberately left in place.
    for location in staged.locations:
        if location.name == mirror.name:
            continue
        copy = _replica_dir(location, bag or "")
        if copy.exists():
            shutil.rmtree(copy)

    still_elsewhere = [
        location.name
        for location in staged.locations
        if location.name != mirror.name and _replica_dir(location, bag or "").exists()
    ]
    landed = store.is_tombstoned(bag or "") and replica.exists() and not still_elsewhere
    if not landed:
        if not store.is_tombstoned(bag or ""):
            reason = "the tombstone read back absent from the archive's own store"
        elif not replica.exists():
            reason = f"mirror {mirror.name!r} no longer holds the stale copy the scenario needs"
        else:
            reason = (
                "the takedown left a copy at "
                + ", ".join(repr(name) for name in still_elsewhere)
                + ", so the surviving copy is not the offline mirror's"
            )
        return Injection(
            description="the stale-replica state was not reached",
            landed=False,
            bags_touched=0,
            reason=reason,
        )
    return Injection(
        description=(
            f"the record was taken down and removed everywhere reachable, while mirror "
            f"{mirror.name!r} kept its copy as an offline box would"
        ),
        landed=True,
        bags_touched=1,
        reason="",
    )


def _a_location_still_holds_a_taken_down_bag(staged: StagedArchive) -> bool:
    """The archive's own takedown bookkeeping sees a stale copy still on disk.

    Routed through :meth:`~ledger.tombstones.TombstoneStore.pending_for`, which is
    what the archive uses to answer "which locations have not applied this
    removal" — the same honesty ``/consent-status`` reports from. It reads the
    store and the tree, never the injection's own account of itself.
    """

    store = _tombstone_store(staged)
    for location in staged.locations:
        root = Path(location.path)
        if not root.exists():
            continue
        for record_id in store.pending_for(location.name):
            if (root / record_id).exists():
                return True
    return False


def _every_stale_copy_is_gone_and_recorded(staged: StagedArchive) -> bool:
    """The inverse of :func:`_every_replica_is_whole`, and deliberately so.

    For the damage scenarios a recovery is credited when every replica is back.
    Here it is credited only when no reachable location holds a taken-down record
    *and* every reachable location has a receipt saying so. A sweep that deleted
    the copies without recording confirmations would leave the archive unable to
    say a removal had been applied, and `/consent-status` would keep reporting it
    as pending forever.
    """

    store = _tombstone_store(staged)
    tombstones = store.all()
    if not tombstones:
        # Nothing was taken down, so there is nothing this could be evidence of.
        return False
    for location in staged.locations:
        root = Path(location.path)
        if not root.exists():
            continue
        if store.pending_for(location.name):
            return False
        for tombstone in tombstones:
            if (root / tombstone.record_id).exists():
                return False
    return True


def _heal_the_taken_down_bag(staged: StagedArchive) -> None:
    """``ledger heal --id <the taken-down record>``, for each tombstoned record.

    Not :func:`_heal_the_first_bag`: by the time recovery runs, the takedown has
    already emptied the authoritative store, so "the first bag" is a different bag
    or no bag at all. The id comes from the archive's own tombstone store rather
    than from a variable this module carried across steps, which is also what a
    steward reading ``ledger replicas`` would type.
    """

    store = _tombstone_store(staged)
    for record_id in sorted(tombstone.record_id for tombstone in store.all()):
        heal(
            record_id,
            list(staged.locations),
            agent=DRILL_AGENT,
            now=now_iso(),
            tombstones=store,
        )


def _no_tombstoned_bag_survives_in_the_store(staged: StagedArchive) -> bool:
    """``stale-replica``'s source check: the taken-down bag is gone and stays gone.

    Every other scenario ends by asserting the authoritative bag still validates.
    This one asserts it is *absent*, because the recovery's job here was to refuse
    to bring it back. Running the generic check would have marked a correct
    refusal as a failure — and, worse, a heal that quietly resurrected the record
    from the stale mirror would have satisfied it.
    """

    store = _tombstone_store(staged)
    tombstones = store.all()
    if not tombstones:
        return False
    bags = staged.store_root / "bags"
    return all(not (bags / tombstone.record_id).exists() for tombstone in tombstones)


def _bit_rot_scenario() -> Scenario:
    return Scenario(
        name="bit-rot",
        fault="bytes flipped in one replica's payload",
        recovering_command="ledger heal",
        applicability=_needs_a_bag_and_a_mirror,
        inject=_inject_bit_rot,
        detect=_replicas_report_a_problem,
        recover=_heal_the_first_bag,
        verify=_every_replica_is_whole,
    )


def _lost_location_scenario() -> Scenario:
    def applicability(staged: StagedArchive) -> str | None:
        blocked = _needs_a_bag_and_a_mirror(staged)
        if blocked is not None:
            return blocked
        if len(staged.locations) < 2:
            return (
                "the archive has only one location, so losing one leaves nothing to "
                "recover from; add a second location to rehearse this"
            )
        return None

    return Scenario(
        name="lost-location",
        fault="one mirror's copy of the bag removed",
        recovering_command="ledger heal",
        applicability=applicability,
        inject=_inject_lost_location,
        detect=_replicas_report_a_problem,
        recover=_heal_the_first_bag,
        verify=_every_replica_is_whole,
    )


def _truncated_log_scenario() -> Scenario:
    return Scenario(
        name="truncated-log",
        fault="the PREMIS chain cut short in one replica",
        recovering_command="ledger heal",
        applicability=_needs_a_bag_and_a_mirror,
        inject=_inject_truncated_log,
        detect=_replicas_report_a_problem,
        recover=_heal_the_first_bag,
        verify=_every_replica_is_whole,
    )


def _stale_replica_scenario() -> Scenario:
    """An older copy reattaching after a takedown, and a recovery that says no.

    Structurally the odd one out, and it has to be. The other three ask "did the
    archive come back"; this one asks "did the archive *refuse* to bring something
    back". So ``verify`` looks for absence rather than presence, and
    ``source_check`` is inverted: the taken-down bag must not be in the store when
    this ends. Passing the tombstone store to ``heal`` is what makes the refusal
    happen at all, and it is exactly what ``ledger heal`` does.
    """

    def applicability(staged: StagedArchive) -> str | None:
        if _first_bag(staged) is None:
            return "the archive holds no bags, so there is nothing to take down"
        if not _mirrors(staged):
            return (
                "the archive has no mirror location, so no copy can outlive a takedown "
                "the way an offline replica's does"
            )
        return None

    return Scenario(
        name="stale-replica",
        fault="an offline mirror keeps its copy of a taken-down record",
        recovering_command="ledger heal",
        applicability=applicability,
        inject=_inject_stale_replica,
        detect=_a_location_still_holds_a_taken_down_bag,
        recover=_heal_the_taken_down_bag,
        verify=_every_stale_copy_is_gone_and_recorded,
        source_check=_no_tombstoned_bag_survives_in_the_store,
    )


def _source_bag_still_validates(staged: StagedArchive) -> bool:
    """The authoritative copy under ``bags/`` is intact.

    The default :attr:`Scenario.source_check`, run as the last step alongside the
    replica sweep: a heal that repaired the mirrors by damaging the source would
    otherwise pass. ``stale-replica`` replaces it, because there the source is
    meant to be gone.
    """

    bag = _first_bag(staged)
    if bag is None:  # pragma: no cover - applicability already refused this
        return False
    try:
        return validate_bag(staged.store_root / "bags" / bag).ok
    except Exception:
        return False


#: Every scenario this drill knows, by name. A closed registry: ``--scenario``
#: rejects anything not listed here rather than silently rehearsing nothing.
SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in (
        _bit_rot_scenario(),
        _lost_location_scenario(),
        _truncated_log_scenario(),
        _stale_replica_scenario(),
    )
}


# --- the runner ----------------------------------------------------------------------


def run_scenario(scenario: Scenario, staged: StagedArchive) -> ScenarioResult:
    """Rehearse one scenario against ``staged``, in the one order that is honest.

    Applicability, then injection, then *proof the injection landed*, then *proof
    the archive's own check saw it*, and only then the recovery. Each of the first
    four can end the scenario, and each ends it with a different word.
    """

    blocked = scenario.applicability(staged)
    if blocked is not None:
        return ScenarioResult(
            scenario=scenario.name,
            outcome=DrillOutcome.NOT_APPLICABLE,
            fault=scenario.fault,
            fault_landed=False,
            fault_detected=False,
            bags_touched=0,
            recovering_command=scenario.recovering_command,
            detail=blocked,
            failing_step="applicability",
        )

    injection = scenario.inject(staged)
    if not injection.landed:
        return ScenarioResult(
            scenario=scenario.name,
            outcome=DrillOutcome.FAILED,
            fault=scenario.fault,
            fault_landed=False,
            fault_detected=False,
            bags_touched=injection.bags_touched,
            recovering_command=scenario.recovering_command,
            detail=(
                "the fault was not injected, so nothing was rehearsed: "
                f"{injection.reason or injection.description}. A drill that reports a "
                "recovery from damage it never caused is worse than no drill."
            ),
            failing_step="inject",
        )

    detected = scenario.detect(staged)
    if not detected:
        return ScenarioResult(
            scenario=scenario.name,
            outcome=DrillOutcome.FAILED,
            fault=scenario.fault,
            fault_landed=True,
            fault_detected=False,
            bags_touched=injection.bags_touched,
            recovering_command=scenario.recovering_command,
            detail=(
                f"the fault landed ({injection.description}) but the archive's own "
                "check did not report it. Recovery is not attempted and cannot be "
                "credited: a repair nothing would ever call is not a recovery path."
            ),
            failing_step="detect",
        )

    try:
        scenario.recover(staged)
    except Exception as error:
        return ScenarioResult(
            scenario=scenario.name,
            outcome=DrillOutcome.FAILED,
            fault=scenario.fault,
            fault_landed=True,
            fault_detected=True,
            bags_touched=injection.bags_touched,
            recovering_command=scenario.recovering_command,
            detail=f"the recovery command raised: {type(error).__name__}",
            failing_step="recover",
        )

    whole = scenario.verify(staged) and scenario.source_check(staged)
    return ScenarioResult(
        scenario=scenario.name,
        outcome=DrillOutcome.RECOVERED if whole else DrillOutcome.FAILED,
        fault=scenario.fault,
        fault_landed=True,
        fault_detected=True,
        bags_touched=injection.bags_touched,
        recovering_command=scenario.recovering_command,
        detail=(
            f"{injection.description}; detected, then repaired, and every replica and "
            "the source bag validate again."
            if whole
            else f"{injection.description}; detected, but the archive did not come back "
            "whole after the recovery command."
        ),
        failing_step=None if whole else "verify",
    )


def run_drill(
    live_root: Path,
    workdir: Path,
    *,
    scenarios: Sequence[str] | None = None,
    now: str | None = None,
) -> DrillReport:
    """Rehearse every named scenario against a scratch copy of ``live_root``.

    Each scenario gets its **own** staged copy, so one scenario's damage cannot
    become another's starting point and a failure part-way through the list does
    not change what the later scenarios are testing.

    The live archive's tree digest is taken before the first scenario and after
    the last, and both appear in the report. That is the drill checking itself:
    the one thing this command must never do is write to the archive it is
    rehearsing.
    """

    names = list(SCENARIOS) if scenarios is None else list(scenarios)
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise DrillError(
            f"unknown scenario(s): {', '.join(sorted(unknown))}; "
            f"known scenarios: {', '.join(sorted(SCENARIOS))}"
        )
    if not (live_root / CONFIG_RELATIVE).is_file():
        raise DrillError(f"no ledger archive at {live_root} (no {CONFIG_RELATIVE})")

    before = tree_digest(live_root)
    config = Config.load(live_root / CONFIG_RELATIVE)
    workdir.mkdir(parents=True, exist_ok=True)

    results: list[ScenarioResult] = []
    for name in names:
        scenario_dir = workdir / name
        if scenario_dir.exists():
            shutil.rmtree(scenario_dir)
        scenario_dir.mkdir(parents=True)
        staged = stage(live_root, scenario_dir)
        results.append(run_scenario(SCENARIOS[name], staged))
    after = tree_digest(live_root)

    return DrillReport(
        generated_date=(now or now_iso()),
        archive_name=config.archive_name,
        results=tuple(results),
        live_digest_before=before,
        live_digest_after=after,
    )


#: Prefix both report copies share, so :func:`latest_drill` can find them without
#: knowing the date.
REPORT_PREFIX = "recovery-drill-"


def write_report(report: DrillReport, audits_dir: Path) -> Path:
    """Write the dated report into ``audits/``, as Markdown and as JSON.

    Two copies of the same run: the Markdown is what a steward reads, and the JSON
    is what :func:`latest_drill` reads back. ``checkup`` parses the JSON rather
    than scraping the prose, so a wording change in the report can never silently
    alter what the readiness check believes about the last drill.
    """

    audits_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_date.split("T")[0]
    path = audits_dir / f"{REPORT_PREFIX}{stamp}.md"
    path.write_text(report.to_markdown(), encoding="utf-8")
    (audits_dir / f"{REPORT_PREFIX}{stamp}.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report.report_path = path
    return path


@dataclass(frozen=True)
class DrillSummary:
    """What a past drill recorded, read back from ``audits/``."""

    generated_date: str
    recovered: tuple[str, ...]
    failed: tuple[str, ...]
    not_applicable: tuple[str, ...]
    live_archive_untouched: bool


def latest_drill(audits_dir: Path) -> DrillSummary | None:
    """The most recent drill recorded under ``audits/``, or ``None`` if there is none.

    ``None`` means no drill has been run *and recorded here*. It does not mean the
    recovery paths are broken and it does not mean they work; a caller must render
    it as neither. That is why this returns ``None`` rather than an empty summary,
    which would read as a drill in which nothing failed.
    """

    if not audits_dir.is_dir():
        return None
    reports = sorted(audits_dir.glob(f"{REPORT_PREFIX}*.json"))
    if not reports:
        return None
    try:
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    scenarios = data.get("scenarios")
    rows = scenarios if isinstance(scenarios, list) else []

    def named(outcome: str) -> tuple[str, ...]:
        return tuple(
            str(row.get("scenario", ""))
            for row in rows
            if isinstance(row, dict) and row.get("outcome") == outcome
        )

    return DrillSummary(
        generated_date=str(data.get("generated_date", "")),
        recovered=named(DrillOutcome.RECOVERED.value),
        failed=named(DrillOutcome.FAILED.value),
        not_applicable=named(DrillOutcome.NOT_APPLICABLE.value),
        live_archive_untouched=bool(data.get("live_archive_untouched", False)),
    )
