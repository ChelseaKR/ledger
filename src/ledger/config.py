"""Versioned configuration as data, not code (administrability/configurability).

An archive's identity — its name, where it stores bags and the identity vault, the
replica locations it mirrors to, its default disclosure policy, its controlled
content-warning vocabulary, and the languages it serves — lives in one declarative,
versioned file rather than scattered through the code. A steward edits a file; no
redeploy is needed (config-over-code).

Two qualities are designed in here on purpose:

* ``schema_version`` plus a migration shim lets the on-disk format evolve without
  stranding older files: an older config is upgraded in memory on load, and a file
  written by a *newer* ledger is refused rather than silently misread
  (upgradability/evolvability, safety).
* :meth:`Config.default` produces secure, single-box defaults — store and vault under
  one root, the *narrowest* disclosure policy (``SEALED_UNTIL``), one local storage
  location — so a community can stand the archive up on one inexpensive machine
  (affordability/installability) without first becoming a security expert (safety:
  default to narrowest).

This module never reads or writes contributor identity or any sealed value: a config
file describes *where* the vault lives, never *what* is in it (the no-outing rule).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ledger.errors import ConfigError
from ledger.models import AccessPolicy

# Current on-disk schema version. Bumped whenever the serialized shape changes; the
# migration shim in :meth:`Config.from_dict` upgrades anything older to this.
CONFIG_SCHEMA_VERSION: int = 1

# The roles a storage location may play. ``local`` is the authoritative on-box copy;
# ``mirror`` is a replica target (replication/redundancy).
_KNOWN_KINDS: frozenset[str] = frozenset({"local", "mirror"})

# A small, opinionated starter vocabulary for content warnings. It is intentionally
# editable: stewards extend it for their community, but a fresh archive is never
# left with an empty controlled vocabulary (usability, care for readers).
_STARTER_CONTENT_WARNINGS: tuple[str, ...] = (
    "violence",
    "sexual-violence",
    "abuse",
    "self-harm",
    "suicide",
    "medical",
    "death",
    "incarceration",
    "deportation",
    "outing",
    "hate-speech",
    "substance-use",
)

# A small, opinionated starter vocabulary for SEALED_CONDITIONAL *conditions* — the
# named events that, once attested by stewards, open a "sealed until a condition is
# met" field (:mod:`ledger.attest`, :func:`ledger.access.policy.is_visible`). Like the
# content-warning vocabulary it is intentionally editable: a community extends it for
# its own promises, but a fresh archive is never left with an empty vocabulary, so the
# tier has real, name-checkable conditions instead of free-text a typo could break.
DEFAULT_CONDITIONS: tuple[str, ...] = (
    "death-of-contributor",
    "group-dissolved",
    "estate-cleared",
    "contributor-consents-release",
    "legal-hold-lifted",
)


@dataclass
class StorageLocation:
    """A role-labelled place the archive keeps bags (redundancy, replication).

    ``kind`` is ``local`` for the authoritative on-box store or ``mirror`` for a
    replica target, so replication code can tell originals from copies by role
    rather than by guessing from the path (inspectability).
    """

    name: str
    path: str
    kind: str = "local"

    def validate(self) -> None:
        """Raise :class:`ConfigError` if the location is malformed.

        Correctness: a typo'd ``kind`` or a nameless/pathless location is caught at
        load time, not at the moment a replica push fails far downstream.
        """
        if not self.name:
            raise ConfigError("storage location has an empty name")
        if not self.path:
            raise ConfigError(f"storage location {self.name!r} has an empty path")
        if self.kind not in _KNOWN_KINDS:
            raise ConfigError(
                f"storage location {self.name!r} has unknown kind {self.kind!r}; "
                f"expected one of {sorted(_KNOWN_KINDS)}"
            )

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain JSON-/TOML-ready mapping."""
        return {"name": self.name, "path": self.path, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StorageLocation:
        """Rebuild from a mapping, coercing values to ``str``.

        Robustness: a JSON/TOML scalar is normalised to text so a non-string value
        in the file becomes a clear validation error rather than a later type bug.
        """
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            kind=str(data.get("kind", "local")),
        )


@dataclass
class Config:
    """The whole archive configuration, as one versioned, declarative object.

    Every field has a secure default (see :meth:`default`) so an archive is runnable
    from a minimal file, yet :meth:`validate` enforces the few invariants that must
    hold for the rest of the system to behave predictably (correctness).
    """

    archive_name: str
    store_root: str
    vault_path: str
    locations: list[StorageLocation] = field(default_factory=list)
    default_policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    content_warnings: list[str] = field(default_factory=list)
    # The controlled vocabulary of SEALED_CONDITIONAL conditions this archive
    # recognises. A steward may only attest a condition drawn from this list, so a
    # typo can never invent an ungoverned condition that quietly opens a seal
    # (correctness, mirrors ``content_warnings``).
    conditions: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["en"])
    # Public-facing governance/operator text (user research P0-4). These power the
    # on-site About/Governance/How-it-works pages so the at-risk contributor can see
    # who runs the archive and how they are held accountable — instead of an
    # unverifiable footer line. All are plain strings a steward edits in the config.
    about: str = ""
    operators: str = ""
    steward_vetting: str = ""
    consent_response_time: str = ""
    contact: str = ""
    # Dual-control: how many DISTINCT stewards must approve a high-stakes action
    # (takedown, identity-unseal, publish-to-public) before it executes. 1 (the
    # default) is single-steward — no change to existing behaviour; a community sets
    # 2+ to require co-approval, so no one steward can act alone (user research D1).
    dual_control_threshold: int = 1
    schema_version: int = CONFIG_SCHEMA_VERSION

    def validate(self) -> None:
        """Raise :class:`ConfigError` if the configuration is inconsistent.

        Checks, in order of how badly they would mislead the rest of the system:

        * an unknown/forward-incompatible ``schema_version`` (a file from a newer
          ledger we cannot safely interpret) — refused, never guessed (safety);
        * an empty ``archive_name`` (every record and bag is stamped with it);
        * a ``default_policy`` outside the documented vocabulary;
        * each storage location's own invariants.

        Correctness/predictability: validation is centralised so callers can trust a
        loaded ``Config`` without re-checking it.
        """
        if self.schema_version > CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config schema_version {self.schema_version} is newer than this "
                f"build supports ({CONFIG_SCHEMA_VERSION}); upgrade ledger to read it"
            )
        if not self.archive_name:
            raise ConfigError("archive_name must not be empty")
        # An empty store_root or vault_path silently resolves to the current working
        # directory — a footgun that would scatter an archive's bags and vault into
        # whatever directory ledger happened to run from. Refuse it (correctness).
        if not self.store_root:
            raise ConfigError("store_root must not be empty")
        if not self.vault_path:
            raise ConfigError("vault_path must not be empty")
        if not isinstance(self.default_policy, AccessPolicy):
            raise ConfigError(f"unknown default_policy: {self.default_policy!r}")
        if self.dual_control_threshold < 1:
            raise ConfigError("dual_control_threshold must be at least 1")
        # A blank or whitespace-only condition would be un-attestable and could shadow
        # a real one; reject it at load so the vocabulary stays a clean, name-checkable
        # set (correctness — same care the rest of the config takes with its lists).
        for condition in self.conditions:
            if not condition or not condition.strip():
                raise ConfigError("conditions must not contain an empty entry")
        for location in self.locations:
            location.validate()

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-ready mapping with a deterministic field order.

        The ``schema_version`` is written so a later ledger can migrate this file
        (upgradability). Determinism: no clock or random source is consulted.
        """
        return {
            "schema_version": self.schema_version,
            "archive_name": self.archive_name,
            "store_root": self.store_root,
            "vault_path": self.vault_path,
            "locations": [loc.to_dict() for loc in self.locations],
            "default_policy": self.default_policy.value,
            "content_warnings": list(self.content_warnings),
            "conditions": list(self.conditions),
            "languages": list(self.languages),
            "about": self.about,
            "operators": self.operators,
            "steward_vetting": self.steward_vetting,
            "consent_response_time": self.consent_response_time,
            "contact": self.contact,
            "dual_control_threshold": self.dual_control_threshold,
        }

    def save(self, path: Path) -> None:
        """Write pretty, sorted JSON atomically to ``path``.

        Atomic rename -> integrity, fault-tolerance: the config is written to a
        sibling temp file in the same directory and then renamed over the target, so
        a crash mid-write can never leave a half-written, unparseable config in place
        — readers see either the old file or the complete new one.
        """
        self.validate()
        # load() can READ .toml (via tomllib), but the standard library has no TOML
        # writer. Rather than silently write JSON bytes into a .toml file (which
        # would then fail to round-trip), refuse it with a clear message
        # (correctness, least surprise).
        if path.suffix == ".toml":
            raise ConfigError(
                "TOML output is not supported; write a .json config "
                "(TOML configs can still be read by load())"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Config:
        """Build a validated ``Config`` from a mapping, migrating older schemas.

        Migration shim (upgradability/evolvability): a file whose ``schema_version``
        is *older* than this build is upgraded in memory via :func:`_migrate` and
        re-stamped with the current version, so old files keep working across
        releases. A file from a *newer* ledger is refused by :meth:`validate`, since
        we cannot safely interpret a format we do not know (safety, correctness).
        """
        raw_version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise ConfigError(f"schema_version must be an integer: {raw_version!r}")
        version = raw_version

        if version > CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config schema_version {version} is newer than this build supports "
                f"({CONFIG_SCHEMA_VERSION}); upgrade ledger to read it"
            )

        migrated = _migrate(dict(data), version)

        policy_value = migrated.get("default_policy", AccessPolicy.SEALED_UNTIL.value)
        try:
            default_policy = AccessPolicy(str(policy_value))
        except ValueError as exc:
            raise ConfigError(f"unknown default_policy: {policy_value!r}") from exc

        locations = [
            StorageLocation.from_dict(item)
            for item in _as_dict_list(migrated.get("locations", []), "locations")
        ]

        config = cls(
            archive_name=str(migrated.get("archive_name", "")),
            store_root=str(migrated.get("store_root", "")),
            vault_path=str(migrated.get("vault_path", "")),
            locations=locations,
            default_policy=default_policy,
            content_warnings=[str(w) for w in _as_list(migrated.get("content_warnings", []))],
            conditions=[str(c) for c in _as_list(migrated.get("conditions", []))],
            languages=[str(lang) for lang in _as_list(migrated.get("languages", ["en"]))],
            about=str(migrated.get("about", "")),
            operators=str(migrated.get("operators", "")),
            steward_vetting=str(migrated.get("steward_vetting", "")),
            consent_response_time=str(migrated.get("consent_response_time", "")),
            contact=str(migrated.get("contact", "")),
            dual_control_threshold=int(str(migrated.get("dual_control_threshold", 1))),
            schema_version=CONFIG_SCHEMA_VERSION,
        )
        config.validate()
        return config

    @classmethod
    def load(cls, path: Path) -> Config:
        """Read a config file from ``path`` (JSON, or TOML when it ends in ``.toml``).

        Interoperability: stewards who prefer TOML for hand-editing get it for free,
        while the canonical machine-written form stays JSON. A parse failure becomes
        a :class:`ConfigError` naming the path and condition, never the file's bytes.
        """
        try:
            data: object
            if path.suffix == ".toml":
                with path.open("rb") as handle:
                    data = tomllib.load(handle)
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {path}") from exc
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"config file {path} is not valid: {exc}") from exc

        if not isinstance(data, dict):
            raise ConfigError(f"config file {path} must contain a mapping at top level")
        return cls.from_dict(data)

    @classmethod
    def default(cls, archive_name: str, root: Path) -> Config:
        """A secure, single-box default configuration under one ``root``.

        Affordability/installability: store and vault live beneath ``root`` so the
        whole archive is one self-contained directory tree on one machine. Safety
        (default to narrowest): the default disclosure policy is ``SEALED_UNTIL``, so
        a freshly stood-up archive reveals nothing until a steward consciously opens
        it. One ``local`` storage location named ``primary`` is provided as the
        authoritative copy; mirrors are added later.

        A storage location's ``path`` is the directory that *directly contains bag
        directories*, which for the local store is ``store/bags`` (where the Archive
        writes them) — not ``store`` itself. Pointing ``primary`` there is what lets
        ``ledger replicas`` and the replication layer find the authoritative bags
        instead of reporting the healthy primary as missing (correctness).
        """
        return cls(
            archive_name=archive_name,
            store_root=str(root / "store"),
            vault_path=str(root / "identity.vault"),
            locations=[
                StorageLocation(name="primary", path=str(root / "store" / "bags"), kind="local")
            ],
            default_policy=AccessPolicy.SEALED_UNTIL,
            content_warnings=list(_STARTER_CONTENT_WARNINGS),
            conditions=list(DEFAULT_CONDITIONS),
            languages=["en"],
            about=(
                "This is a community-governed archive. Records are preserved with "
                "fixity-checked, content-addressed BagIt packaging, and access is "
                "consent-based: a contributor decides what is public, community-only, "
                "restricted to stewards, or sealed. A contributor's identity is stored "
                "separately, encrypted, and is never shown on any page."
            ),
            operators=(
                "Edit this in your config to name the collective or people who run this "
                "archive, so contributors can see who they are trusting."
            ),
            steward_vetting=(
                "Describe how stewards are chosen and held accountable. Stewards can read "
                "access-restricted content (but never a contributor's sealed identity); "
                "content sealed with the 'sealed' policy is restricted from everyone."
            ),
            consent_response_time="We aim to respond to consent and takedown requests within 7 days.",
            contact="Set a contact path (email or secure form) in your config.",
            schema_version=CONFIG_SCHEMA_VERSION,
        )


def _migrate(data: dict[str, object], from_version: int) -> dict[str, object]:
    """Upgrade a raw config mapping from ``from_version`` to the current schema.

    Evolvability: each future schema bump adds one ordered, well-named step here, so
    a chain of upgrades replays deterministically from any supported older version.
    At schema 1 there is nothing earlier to migrate from, so this is the identity
    transform; the structure exists now so the first real bump is a one-line change.
    """
    # Example of the intended shape for future versions:
    #   if from_version < 2:
    #       data = _migrate_1_to_2(data)
    return data


def _as_list(value: object) -> list[object]:
    """Coerce a JSON/TOML value to a list, rejecting non-sequences.

    Robustness: a scalar where a list is expected becomes a clear ``ConfigError``
    instead of iterating a string character by character.
    """
    if isinstance(value, list):
        return value
    raise ConfigError(f"expected a list, got {type(value).__name__}")


def _as_dict_list(value: object, field_name: str) -> list[dict[str, object]]:
    """Coerce a value to a list of mappings, naming the field on failure."""
    result: list[dict[str, object]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            raise ConfigError(f"{field_name} must be a list of mappings; got {type(item).__name__}")
        result.append(dict(item))
    return result
