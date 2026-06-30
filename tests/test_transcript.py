"""Tests for transcripts/captions as first-class payload metadata (backlog H3).

A transcript makes audio or video available to a Deaf or hard-of-hearing reader.
These tests pin that a transcript round-trips through ingest and serialization, is
surfaced on the record page, and that ingesting audio/video without one prints an
accessibility advisory (a nudge, not a block).
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from ledger import cli
from ledger.access.grants import anonymous
from ledger.config import Config
from ledger.ingest import Archive, deserialize_record, serialize_record
from ledger.models import AccessPolicy, ContentAddress, HashAlgo, PayloadFile
from ledger.render import _record_main_html

_TRANSCRIPT = "Hello — this is the spoken transcript."


def test_transcript_round_trips_through_serialization() -> None:
    """A payload's transcript survives serialize -> deserialize."""
    payload = PayloadFile(
        filename="clip.mp3",
        address=ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64),
        media_type="audio/mpeg",
        policy=AccessPolicy.PUBLIC,
        transcript=_TRANSCRIPT,
    )
    from ledger.models import DublinCore, Record

    record = Record(
        title="Clip",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Clip"]),
        payloads=[payload],
    )
    restored = deserialize_record(serialize_record(record))
    assert restored.payloads[0].transcript == _TRANSCRIPT


def _ingest_audio(tmp_path: Path, *, transcript: str | None) -> tuple[Archive, str]:
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "AV"]) == 0
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3 fake audio bytes")
    argv = [
        "ingest",
        "--root",
        str(root),
        "--title",
        "Recording",
        str(clip),
        "--actor",
        "s",
        "--now",
        "2026-01-01T00:00:00Z",
    ]
    if transcript is not None:
        argv += ["--transcript", f"clip.mp3={transcript}"]
    assert cli.main(argv) == 0
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    return archive, rid


def test_ingested_transcript_is_stored_and_rendered(tmp_path: Path) -> None:
    """A transcript given at ingest is stored on the A/V payload and shown on the page."""
    archive, rid = _ingest_audio(tmp_path, transcript=_TRANSCRIPT)
    payload = archive.get(rid).payloads[0]
    assert payload.media_type.startswith("audio/")  # recognised as audio
    assert payload.transcript == _TRANSCRIPT

    disclosed = archive.disclose(rid, anonymous(), now="2026-01-02T00:00:00Z")
    html = _record_main_html(disclosed, proceed=True)
    assert "Transcript" in html
    assert _TRANSCRIPT in html


def test_audio_without_transcript_shows_a_missing_note(tmp_path: Path) -> None:
    """An audio payload with no transcript renders a visible 'no transcript' note."""
    archive, rid = _ingest_audio(tmp_path, transcript=None)
    disclosed = archive.disclose(rid, anonymous(), now="2026-01-02T00:00:00Z")
    html = _record_main_html(disclosed, proceed=True)
    assert "No transcript provided" in html


def test_ingest_warns_on_audio_without_transcript(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ingesting audio/video without a transcript prints an accessibility advisory."""
    _ingest_audio(tmp_path, transcript=None)
    err = capsys.readouterr().err
    assert "no transcript" in err
    assert "WCAG 1.2" in err
