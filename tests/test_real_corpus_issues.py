"""Regression tests for the four defects the real-corpus run filed as issues.

Each of these was found by running the pipeline over 679 real files from the Open
Preservation Foundation ``format-corpus`` (see ``docs/REAL-CORPUS-REPORT.md``), and
each is pinned here with byte prefixes taken verbatim from those files so the fix
cannot regress without the corpus in hand:

* **#142** — the at-risk advisory caught 25 endangered files and flagged 0 of the 66
  unambiguously obsolete ones. Risk now splits into ``at_risk`` (known-obsolete) and
  ``unassessable`` (nothing could be determined), per ADR 0010.
* **#141** — a sealed 157 MB payload peaked at 1189 MB RSS against five documented
  "never held in RAM" claims. SEALED payloads are now size-capped (ADR 0011).
* **#143** — BagIt manifests were not percent-encoded per RFC 8493 §2.1.3, so a
  conformant reader mis-resolved any path containing ``%``.
* **#144** — the record's media type came from a filename guess and could contradict
  the record's own preservation log, for 100 payloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.bag import (
    _decode_manifest_path,
    _encode_manifest_path,
    migrate_manifest_encoding,
    validate_bag,
    write_bag,
)
from ledger.config import DEFAULT_SEALED_PAYLOAD_MAX_BYTES, Config
from ledger.errors import LedgerError
from ledger.ingest import Archive, deserialize_record, serialize_record
from ledger.metadata.premis import PremisLog
from ledger.models import AccessPolicy, PayloadFile, PremisEventType, Record
from ledger.preservation import identify_format

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-01-01T00:00:00Z"

# Header bytes copied verbatim from the corpus files named beside each one, and
# cross-checked against PRONOM's published DROID signature file (V120). A PUID is a
# claim about an external registry, so these are pinned rather than trusted.
_LOTUS_WK1 = b"\x00\x00\x02\x00\x06\x04\x06\x00\x08\x00" + b"\x00" * 16  # KSBASE.WK1
_LOTUS_WKS = b"\x00\x00\x02\x00\x04\x04\x06\x00" + b"\x00" * 16  # testLotus123.wks
_LOTUS_123 = b"\x00\x00\x1a\x00\x03\x10\x04\x00" + b"\x00" * 16  # testLotus123.123
_QPRO_WQ2 = b"\x00\x00\x02\x00\x21\x51\xcc\x00" + b"\x00" * 16  # KSBASE.WQ2
_QPRO_WB1 = b"\x00\x00\x02\x00\x01\x10\xc9\x00" + b"\x00" * 16  # testQuattro.wb1
_ACCESS_JET3 = b"\x00\x01\x00\x00Standard Jet DB\x00\x00\x00\x00\x00" + b"\x00" * 8  # acc97.mdb
_ACCESS_JET4 = b"\x00\x01\x00\x00Standard Jet DB\x00\x01\x00\x00\x00" + b"\x00" * 8  # reviews.mdb
_WINWRITE = b"\x31\xbe\x00\x00\x00\xab\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 8
_DBTEXTWORKS = b"TBA 020 02/09/95" + b"\x6c\x00\x00\x00" * 4  # vegetables.tba
_LIT = b"ITOLITLS\x01\x00\x00\x00(\x00\x00\x00" + b"\x00" * 8  # Lorem Ipsum.lit
_LRF = b"L\x00R\x00F\x00\x00\x00\xe8\x03\x00\xfe" + b"\x00" * 8  # lorem-ipsum.lrf
_ROCKET = b"\xb0\x0c\xb0\x0c\x02\x00NUVO" + b"\x00" * 16  # Lorem Ipsum.rb
_ARJ = b"\x60\xea\x28\x00\x1e\x06\x01\x00" + b"\x00" * 16  # MAPS.ARJ
_IBM_DCA = b"\x00\x05\xe1\x03\x00\x00\x20\xe2\x05\x00\x01\x51\x01\x00" + b"\x00" * 8
_INDESIGN = b"\x06\x06\xed\xf5\xd8\x1d\x46\xe5\xbd\x31\xef\xe7\xfe\x74\xb7\x1dDOCUMENT"
# A Palm database: 32 bytes of free-text name, then type+creator at offset 60.
_MOBI = b"Lorem_Ipsum".ljust(60, b"\x00") + b"BOOKMOBI" + b"\x00" * 8
_PALMDOC = b"Lorem Ipsum".ljust(60, b"\x00") + b"TEXtREAd" + b"\x00" * 8


# --- #142: the at-risk advisory was blind to obsolete formats ------------------


@pytest.mark.parametrize(
    ("data", "name_fragment", "puid"),
    [
        (_LOTUS_WK1, "Lotus 1-2-3", "x-fmt/114"),
        (_LOTUS_WKS, "Lotus 1-2-3", "x-fmt/117"),
        (_LOTUS_123, "Lotus 1-2-3", "fmt/1452"),
        (_QPRO_WQ2, "Quattro Pro", "x-fmt/122"),
        (_QPRO_WB1, "Quattro Pro", "fmt/834"),
        (_WINWRITE, "Write for Windows", "x-fmt/12"),
        (_LIT, "Microsoft Reader", "fmt/867"),
        (_LRF, "Broad Band eBook", "fmt/518"),
        (_ROCKET, "Rocket eBook", "fmt/485"),
        (_ARJ, "ARJ", "fmt/610"),
        (_IBM_DCA, "IBM DisplayWrite", "x-fmt/148"),
        (_MOBI, "Mobipocket", "fmt/396"),
        (_PALMDOC, "PalmDOC", "fmt/396"),
    ],
)
def test_obsolete_formats_are_identified_and_flagged(
    data: bytes, name_fragment: str, puid: str
) -> None:
    """Each of these was ``Unidentified`` and NOT at-risk before the corpus run.

    The PUID is asserted too: a wrong one does not merely fail to help, it
    misinforms every downstream PRONOM/DROID tool that trusts it (the defect that
    put three wrong identifiers in every PREMIS log in the previous pass).
    """
    fmt = identify_format(data, filename="payload.bin")
    assert name_fragment in fmt.name
    assert fmt.basis == "signature"
    assert fmt.at_risk is True
    assert fmt.unassessable is False
    assert fmt.puid == puid
    assert fmt.recommendation


@pytest.mark.parametrize(("data", "jet"), [(_ACCESS_JET3, "Jet 3"), (_ACCESS_JET4, "Jet 4")])
def test_access_is_named_without_asserting_an_indistinguishable_puid(data: bytes, jet: str) -> None:
    """PRONOM gives each Jet version two byte-identical PUIDs; we assert neither.

    Naming one of a pair at random is a guess dressed as an interoperable fact,
    which is precisely the defect the previous corpus pass found three times.
    """
    fmt = identify_format(data, filename="reviews.mdb")
    assert jet in fmt.name
    assert fmt.at_risk is True
    assert fmt.puid is None


def test_dbtextworks_component_is_named_by_its_own_header() -> None:
    """PRONOM has no entry for DB/TextWorks, so the family is read off its header."""
    fmt = identify_format(_DBTEXTWORKS, filename="vegetables.tba")
    assert "DB/TextWorks" in fmt.name
    assert "(TBA)" in fmt.name
    assert fmt.at_risk is True
    assert fmt.puid is None


def test_dbtextworks_shape_check_does_not_claim_ordinary_text() -> None:
    """A text file opening with a component tag is not a catalogue index.

    The header is a *shape* — three letters, space, three digits, space, MM/DD/YY —
    not a four-byte prefix, so this stays plain text.
    """
    fmt = identify_format(b"OCC is the name of our reading group.\n", filename="notes.txt")
    assert fmt.at_risk is False
    assert "DB/TextWorks" not in fmt.name


def test_indesign_names_the_format_but_asserts_no_version_puid() -> None:
    """Every InDesign PUID from fmt/196 on shares one head signature."""
    fmt = identify_format(_INDESIGN + b"\x00" * 8, filename="flyer.indd")
    assert "InDesign" in fmt.name
    assert fmt.at_risk is True
    assert fmt.puid is None


def test_unknown_is_unassessable_but_not_at_risk() -> None:
    """The split that resolves #142: absence of a risk finding is not safety.

    Making ``unknown`` imply ``at_risk`` was rejected because the remedies differ —
    52 of the corpus's 118 unidentified files were empty, OS metadata, damaged, or
    simply niche, and "migrate to a modern format" is wrong for every one of them.
    """
    fmt = identify_format(b"\x7f\x1c\xa3\xff" * 8, filename="mystery.bin")
    assert fmt.basis == "unknown"
    assert fmt.unassessable is True
    assert fmt.at_risk is False
    assert "UNASSESSABLE" in fmt.summary()
    assert "not a finding that the file is safe" in fmt.summary()


def test_at_risk_format_is_not_reported_as_unassessable() -> None:
    """The two signals are orthogonal, not two names for one bucket."""
    fmt = identify_format(_LOTUS_WK1, filename="budget.wk1")
    assert fmt.at_risk is True
    assert fmt.unassessable is False
    assert "AT-RISK" in fmt.summary()
    assert "UNASSESSABLE" not in fmt.summary()


def test_empty_file_is_reported_as_empty_not_unidentified() -> None:
    """31 of the corpus's 118 "unidentified" files were simply zero bytes.

    "We could not identify this" is a much weaker and more alarming statement than
    "this file is empty", which in a real deposit means a truncated transfer.
    """
    fmt = identify_format(b"", filename="oral-history.wav")
    assert fmt.basis == "empty"
    assert "Empty" in fmt.name
    assert fmt.unassessable is False
    assert fmt.at_risk is False


def test_doc_extension_no_longer_asserts_microsoft_ole2() -> None:
    """All five corpus files the ``.doc`` row identified were NOT Microsoft.

    A genuine legacy Office file matches the OLE2 *signature* two steps earlier, so
    the extension row could only ever fire when wrong — and it wrote PUID fmt/111
    into the PREMIS log as fact.
    """
    wordperfect_42 = b"\xcb\x0a\x01\xf6\x01\xcb\xc0\x0a" + b"\x00" * 16
    fmt = identify_format(wordperfect_42, filename="testWordPerfect_42.doc")
    assert fmt.puid != "fmt/111"
    assert fmt.unassessable is True

    displaywrite = identify_format(_IBM_DCA, filename="testIBMDisplayWrite40.doc")
    assert "IBM DisplayWrite" in displaywrite.name


def test_ole2_is_named_for_what_its_puid_actually_covers() -> None:
    """fmt/111 is "OLE2 Compound Document Format" in PRONOM, with no extensions.

    A Quattro Pro .wb3 is an OLE2 file and is not Microsoft anything.
    """
    fmt = identify_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16, filename="x.wb3")
    assert fmt.puid == "fmt/111"
    assert "OLE2 Compound Document" in fmt.name


# --- #144: the record must not contradict its own preservation log -------------


def test_record_media_type_is_the_identifiers_verdict(tmp_path: Path) -> None:
    """``mimetypes.guess_type`` no longer overrides identification (ADR 0010).

    Before: a ``.mobi`` the identifier could not name was still stored as
    ``application/x-mobipocket-ebook``, contradicting its own PREMIS log.
    """
    archive = Archive.init(Config.default("A", tmp_path / "arc"))
    source = tmp_path / "mystery.pdf"
    source.write_bytes(b"\x7f\x1c\xa3\xff" * 64)
    aip = archive.ingest(
        {"mystery.pdf": source},
        Record(title="T", default_policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    payload = aip.record.payloads[0]
    assert payload.media_type == "application/octet-stream"
    assert payload.media_type_basis == "unknown"


def test_registry_media_type_beats_the_stdlib_guess(tmp_path: Path) -> None:
    """The better-informed source was the one being overridden."""
    archive = Archive.init(Config.default("A", tmp_path / "arc"))
    source = tmp_path / "budget.wk1"
    source.write_bytes(_LOTUS_WK1)
    aip = archive.ingest(
        {"budget.wk1": source},
        Record(title="T", default_policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    payload = aip.record.payloads[0]
    assert payload.media_type == "application/vnd.lotus-1-2-3"
    assert payload.media_type_basis == "signature"


def test_declared_media_type_wins_and_says_so(tmp_path: Path) -> None:
    """A steward's declaration is a human assertion, not a guess — but it is labelled."""
    archive = Archive.init(Config.default("A", tmp_path / "arc"))
    source = tmp_path / "scan.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    record = Record(title="T", default_policy=AccessPolicy.PUBLIC)
    record.payloads = [
        PayloadFile(
            filename="scan.png",
            address=Archive.init(Config.default("B", tmp_path / "b")).store.put_bytes(b"x"),
            media_type="image/x-community-scan",
            policy=AccessPolicy.PUBLIC,
        )
    ]
    aip = archive.ingest({"scan.png": source}, record, now=_NOW)
    payload = aip.record.payloads[0]
    assert payload.media_type == "image/x-community-scan"
    assert payload.media_type_basis == "declared"


def test_media_type_basis_round_trips_through_the_record(sample_payload_file: PayloadFile) -> None:
    """A record written today must read back with its provenance intact."""
    record = Record(title="T")
    record.payloads = [
        PayloadFile(
            filename=sample_payload_file.filename,
            address=sample_payload_file.address,
            media_type="text/markdown",
            media_type_basis="extension",
        )
    ]
    restored = deserialize_record(serialize_record(record))
    assert restored.payloads[0].media_type_basis == "extension"


def test_media_type_basis_is_empty_on_a_pre_adr_record() -> None:
    """An older record records no provenance, and "" must not read as verified."""
    older = '{"record_id": "r", "title": "T", "payloads": [{"filename": "a.txt", '
    older += '"address": "sha256:' + "0" * 64 + '", "media_type": "text/plain"}]}'
    restored = deserialize_record(older)
    assert restored.payloads[0].media_type_basis == ""


def test_premis_outcome_distinguishes_unidentified_from_empty(tmp_path: Path) -> None:
    """Three outcomes, three different things for a steward to do."""
    archive = Archive.init(Config.default("A", tmp_path / "arc"))
    good = tmp_path / "scan.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    mystery = tmp_path / "mystery.bin"
    mystery.write_bytes(b"\x7f\x1c\xa3\xff" * 64)
    aip = archive.ingest(
        {"scan.png": good, "empty.wav": empty, "mystery.bin": mystery},
        Record(title="T", default_policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    premis = PremisLog.read(aip.bag.path / "premis.json")
    outcomes = {
        entry.detail.split(" [")[0].removeprefix("identified as "): entry.outcome
        for entry in premis.events
        if entry.event_type is PremisEventType.FORMAT_IDENTIFICATION
    }
    assert outcomes["PNG"] == "success"
    assert outcomes["Empty file (zero bytes)"] == "empty"
    assert outcomes["Unidentified"] == "unidentified"


# --- #143: RFC 8493 §2.1.3 percent-encoding -----------------------------------

# The hostile filenames the corpus's `filesys-trials` collection actually ships.
_HOSTILE_NAMES = ["!", "#", "$", "%", "(.)", "{ (2).}", "`", "~", "null", "%41", "a%20b"]


@pytest.mark.parametrize("name", _HOSTILE_NAMES)
def test_manifest_path_round_trips_hostile_filenames(name: str) -> None:
    """Encode-then-decode is the identity for every name the corpus contains."""
    assert _decode_manifest_path(_encode_manifest_path(name)) == name


def test_only_the_three_rfc_characters_are_encoded() -> None:
    """§2.1.3 names exactly ``%``, CR, and LF — encoding more would mangle UTF-8."""
    assert _encode_manifest_path("a%b") == "a%25b"
    assert _encode_manifest_path("a\rb") == "a%0Db"
    assert _encode_manifest_path("a\nb") == "a%0Ab"
    assert _encode_manifest_path("año (2).txt") == "año (2).txt"


def test_percent_is_encoded_before_the_control_characters() -> None:
    """Escaping ``%`` last would double-encode the escapes just introduced."""
    assert _encode_manifest_path("%\n") == "%25%0A"
    assert _decode_manifest_path("%25%0A") == "%\n"


def test_bag_with_a_percent_named_payload_is_rfc_conformant(tmp_path: Path) -> None:
    """The corpus file literally named ``%`` went into the manifest raw.

    A conformant reader (the LoC ``bagit-python`` reference implementation
    percent-decodes) then resolved it to the wrong file or to none — the one defect
    that attacks bag.py's stated reason for choosing BagIt at all.
    """
    source = tmp_path / "src"
    source.mkdir()
    for name in ("%", "%41", "plain.txt"):
        (source / name).write_bytes(b"corpus bytes")
    bag = write_bag(
        tmp_path / "bag",
        {name: source / name for name in ("%", "%41", "plain.txt")},
    )
    manifest = (bag.path / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert "data/%25\n" in manifest
    assert "data/%2541\n" in manifest
    assert "data/plain.txt\n" in manifest
    # And ledger still reads back what it wrote.
    assert validate_bag(bag.path).ok


def test_legacy_bag_with_a_raw_percent_still_validates(tmp_path: Path) -> None:
    """The migration is by reading, not by a flag day.

    A general percent-decoder would turn a pre-migration payload named ``%41`` into
    a lookup for ``A`` — corrupting every existing bag to fix new ones. Only the
    three escapes the RFC defines are decoded.
    """
    assert _decode_manifest_path("100%25done") == "100%done"
    # ``%zz`` is not an RFC escape, so it is a literal path from an older bag.
    assert _decode_manifest_path("%zz") == "%zz"
    assert _decode_manifest_path("50%") == "50%"


def test_migrate_manifest_encoding_reseals_and_is_idempotent(tmp_path: Path) -> None:
    """Rewriting a payload manifest changes bytes the tag manifests cover."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "%").write_bytes(b"corpus bytes")
    bag = write_bag(tmp_path / "bag", {"%": source / "%"})

    # Forge a pre-migration bag by writing the path back out raw, as ledger used to.
    manifest_path = bag.path / "manifest-sha256.txt"
    digest = manifest_path.read_text(encoding="utf-8").split("  ")[0]
    legacy = f"{digest}  data/%\n"
    manifest_path.write_text(legacy, encoding="utf-8")

    assert migrate_manifest_encoding(bag.path) is True
    assert "data/%25\n" in manifest_path.read_text(encoding="utf-8")
    assert validate_bag(bag.path).ok
    # Idempotent: a second pass must not churn the tag manifests.
    assert migrate_manifest_encoding(bag.path) is False


def test_migrate_leaves_an_ordinary_bag_untouched(tmp_path: Path) -> None:
    """Only an archive holding %/CR/LF in a payload name needs anything."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"jpeg bytes")
    bag = write_bag(tmp_path / "bag", {"photo.jpg": source / "photo.jpg"})
    before = (bag.path / "tagmanifest-sha256.txt").read_bytes()
    assert migrate_manifest_encoding(bag.path) is False
    assert (bag.path / "tagmanifest-sha256.txt").read_bytes() == before


# --- #141: the SEALED memory cap ----------------------------------------------


def test_oversized_sealed_payload_is_refused(tmp_path: Path) -> None:
    """A 157 MB sealed payload peaked at 1189 MB RSS; uncapped it is an OOM kill.

    A refusal that names the cost is strictly better than a process that dies on
    the most sensitive material an at-risk contributor has.
    """
    config = Config.default("A", tmp_path / "arc")
    config.sealed_payload_max_bytes = 1024
    archive = Archive.init(config)
    source = tmp_path / "oral-history.wav"
    source.write_bytes(b"\x00" * 4096)
    with pytest.raises(LedgerError) as excinfo:
        archive.ingest(
            {"oral-history.wav": source},
            Record(title="T", default_policy=AccessPolicy.SEALED),
            vault_key=_VAULT_KEY,
            now=_NOW,
        )
    message = str(excinfo.value)
    assert "oral-history.wav" in message
    assert "SEALED" in message
    assert "sealed_payload_max_bytes" in message


def test_the_refusal_happens_before_anything_is_written(tmp_path: Path) -> None:
    """A precondition failure must leave the store, the bag, and the vault untouched.

    Checking inside the payload loop would already have written ciphertext for
    whichever files sorted first.
    """
    config = Config.default("A", tmp_path / "arc")
    config.sealed_payload_max_bytes = 1024
    archive = Archive.init(config)
    small = tmp_path / "aaa-small.txt"
    small.write_bytes(b"small")
    big = tmp_path / "zzz-big.wav"
    big.write_bytes(b"\x00" * 4096)
    with pytest.raises(LedgerError):
        archive.ingest(
            {"aaa-small.txt": small, "zzz-big.wav": big},
            Record(title="T", default_policy=AccessPolicy.SEALED),
            vault_key=_VAULT_KEY,
            now=_NOW,
        )
    assert not list(archive.bags_dir.iterdir())


def test_the_cap_applies_only_to_the_sealed_tier(tmp_path: Path) -> None:
    """The streamed path is flat in memory whatever the size, so it is not capped."""
    config = Config.default("A", tmp_path / "arc")
    config.sealed_payload_max_bytes = 1024
    archive = Archive.init(config)
    source = tmp_path / "big.bin"
    source.write_bytes(b"\x00" * 4096)
    aip = archive.ingest(
        {"big.bin": source},
        Record(title="T", default_policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    assert aip.record.payloads[0].size_bytes == 4096


def test_a_sealed_payload_within_the_cap_still_ingests(tmp_path: Path) -> None:
    """The cap must not break the tier it protects."""
    archive = Archive.init(Config.default("A", tmp_path / "arc"))
    source = tmp_path / "note.txt"
    source.write_bytes(b"a sealed note")
    aip = archive.ingest(
        {"note.txt": source},
        Record(title="T", default_policy=AccessPolicy.SEALED),
        vault_key=_VAULT_KEY,
        now=_NOW,
    )
    assert aip.record.payloads[0].policy is AccessPolicy.SEALED
    assert validate_bag(aip.bag.path).ok


def test_cap_round_trips_through_the_config_file(tmp_path: Path) -> None:
    """An operator raising the cap must have it survive a save/load."""
    config = Config.default("A", tmp_path / "arc")
    assert config.sealed_payload_max_bytes == DEFAULT_SEALED_PAYLOAD_MAX_BYTES
    config.sealed_payload_max_bytes = 128 * 1024 * 1024
    path = tmp_path / "ledger.json"
    config.save(path)
    assert Config.load(path).sealed_payload_max_bytes == 128 * 1024 * 1024


def test_a_config_written_before_the_cap_existed_takes_the_default(tmp_path: Path) -> None:
    """Older configs load unchanged rather than failing closed on a missing key."""
    path = tmp_path / "ledger.json"
    path.write_text(
        '{"archive_name": "A", "store_root": "s", "vault_path": "v", "schema_version": 1}',
        encoding="utf-8",
    )
    assert Config.load(path).sealed_payload_max_bytes == DEFAULT_SEALED_PAYLOAD_MAX_BYTES
