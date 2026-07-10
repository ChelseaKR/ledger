"""Tests for RM6 — captions/transcripts as a first-class *ingest* step.

Covers the concrete, standards-grounded slice of RM6: parsing an
already-transcribed WebVTT or SRT caption file into structured, timed
:class:`~ledger.models.TranscriptCue` segments (:mod:`ledger.captions`), storing
them on a :class:`~ledger.models.PayloadFile` through the one ingest path, and
disclosing/rendering them. No speech-to-text is exercised or implied anywhere
here — every fixture is a hand-authored caption file, exactly the kind of file a
human transcriber or captioning tool already produces.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from ledger import cli
from ledger.access.grants import anonymous, steward
from ledger.access.policy import disclose
from ledger.captions import (
    cues_to_plain_text,
    parse_captions,
    parse_srt,
    parse_webvtt,
    sniff_caption_format,
)
from ledger.config import Config
from ledger.errors import CaptionParseError
from ledger.ingest import Archive, deserialize_record, serialize_record
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    HashAlgo,
    PayloadFile,
    Record,
    TranscriptCue,
)
from ledger.moderate import set_payload_policy
from ledger.render import _record_main_html

pytestmark = pytest.mark.disclosure

_NOW = "2026-01-02T00:00:00Z"

_VTT = """WEBVTT

NOTE
This whole block, including this comment, must be ignored by the parser.

1
00:00:01.000 --> 00:00:04.000
<v Margit>I remember the winter of 1974.

2
00:00:04.500 --> 00:00:08.250 align:start line:0
It was the year everything changed.
"""

_SRT = """1
00:00:01,000 --> 00:00:04,000
I remember the winter of 1974.

2
00:00:04,500 --> 00:00:08,250
It was the year everything changed.
"""


# --- WebVTT parsing -----------------------------------------------------------


def test_parse_webvtt_returns_cues_in_order() -> None:
    cues = parse_webvtt(_VTT)
    assert [c.text for c in cues] == [
        "I remember the winter of 1974.",
        "It was the year everything changed.",
    ]


def test_parse_webvtt_normalizes_timestamps_with_hours() -> None:
    """A minutes-only source timestamp (no hours) is normalized to HH:MM:SS.mmm."""
    cues = parse_webvtt(_VTT)
    assert cues[0].start == "00:00:01.000"
    assert cues[0].end == "00:00:04.000"
    assert cues[1].end == "00:00:08.250"


def test_parse_webvtt_extracts_voice_span_speaker() -> None:
    """The <v Speaker> voice span names the first cue's speaker; the second has none."""
    cues = parse_webvtt(_VTT)
    assert cues[0].speaker == "Margit"
    assert cues[1].speaker is None


def test_parse_webvtt_strips_cue_settings_after_end_time() -> None:
    """`align:start line:0` after the end timestamp is discarded, not parsed as text."""
    cues = parse_webvtt(_VTT)
    assert "align" not in cues[1].text
    assert "line" not in cues[1].text


def test_parse_webvtt_skips_note_blocks() -> None:
    """A NOTE block is a comment, never surfaced as a cue (spec: 'ignored by the parser')."""
    cues = parse_webvtt(_VTT)
    assert not any("ignored by the parser" in c.text for c in cues)
    assert len(cues) == 2


def test_parse_webvtt_hours_roundtrip() -> None:
    """An explicit-hours timestamp round-trips through normalization unchanged."""
    vtt = "WEBVTT\n\n01:02:03.456 --> 01:02:05.000\nLong recording.\n"
    cues = parse_webvtt(vtt)
    assert cues[0].start == "01:02:03.456"
    assert cues[0].end == "01:02:05.000"


def test_parse_webvtt_rejects_missing_signature() -> None:
    with pytest.raises(CaptionParseError, match="WEBVTT"):
        parse_webvtt("1\n00:00:01.000 --> 00:00:02.000\nhi\n")


def test_parse_webvtt_rejects_end_not_after_start() -> None:
    vtt = "WEBVTT\n\n00:00:05.000 --> 00:00:02.000\nout of order\n"
    with pytest.raises(CaptionParseError, match="end time must be after"):
        parse_webvtt(vtt)


def test_parse_webvtt_rejects_unparsable_timestamp() -> None:
    vtt = "WEBVTT\n\nnotatime --> 00:00:02.000\nhi\n"
    with pytest.raises(CaptionParseError, match="unparsable timestamp"):
        parse_webvtt(vtt)


def test_parse_webvtt_rejects_no_cues() -> None:
    with pytest.raises(CaptionParseError, match="no cues"):
        parse_webvtt("WEBVTT\n")


def test_parse_webvtt_error_never_echoes_cue_text() -> None:
    """A parse error names only the cue index/condition — never the caption prose
    (no-outing-adjacent defense in depth; caption text can be as sensitive as a
    transcript field)."""
    vtt = "WEBVTT\n\n00:00:05.000 --> 00:00:02.000\nA deeply private confession.\n"
    with pytest.raises(CaptionParseError) as exc_info:
        parse_webvtt(vtt)
    assert "private confession" not in str(exc_info.value)


# --- SRT parsing ----------------------------------------------------------


def test_parse_srt_returns_cues_in_order() -> None:
    cues = parse_srt(_SRT)
    assert [c.text for c in cues] == [
        "I remember the winter of 1974.",
        "It was the year everything changed.",
    ]


def test_parse_srt_normalizes_comma_to_dot_timestamps() -> None:
    cues = parse_srt(_SRT)
    assert cues[0].start == "00:00:01.000"
    assert cues[1].end == "00:00:08.250"


def test_parse_srt_never_guesses_a_speaker() -> None:
    """SRT has no standardized speaker syntax; every SRT-derived cue is speaker=None."""
    cues = parse_srt(_SRT)
    assert all(c.speaker is None for c in cues)


def test_parse_srt_tolerates_missing_sequence_number() -> None:
    srt = "00:00:01,000 --> 00:00:02,000\nhi\n"
    cues = parse_srt(srt)
    assert cues[0].text == "hi"


def test_parse_srt_rejects_end_not_after_start() -> None:
    srt = "1\n00:00:05,000 --> 00:00:02,000\nout of order\n"
    with pytest.raises(CaptionParseError, match="end time must be after"):
        parse_srt(srt)


def test_parse_srt_rejects_empty_file() -> None:
    with pytest.raises(CaptionParseError, match="empty"):
        parse_srt("   \n")


def test_parse_srt_rejects_no_cues() -> None:
    with pytest.raises(CaptionParseError):
        parse_srt("not a caption file at all")


# --- sniffing / dispatch ----------------------------------------------------


def test_sniff_caption_format_detects_vtt() -> None:
    assert sniff_caption_format(_VTT) == "vtt"


def test_sniff_caption_format_detects_srt() -> None:
    assert sniff_caption_format(_SRT) == "srt"


def test_sniff_caption_format_ignores_bom() -> None:
    assert sniff_caption_format("﻿WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n") == "vtt"


def test_sniff_caption_format_unrecognised_returns_none() -> None:
    assert sniff_caption_format("just some prose, not a caption file\n") is None


def test_parse_captions_dispatches_by_content_not_extension() -> None:
    """parse_captions sniffs the bytes, mirroring upload.py's magic-byte philosophy —
    it is never told (or trusts) a filename extension."""
    assert [c.text for c in parse_captions(_VTT)] == [c.text for c in parse_webvtt(_VTT)]
    assert [c.text for c in parse_captions(_SRT)] == [c.text for c in parse_srt(_SRT)]


def test_parse_captions_rejects_unrecognised_format() -> None:
    with pytest.raises(CaptionParseError, match="not a recognised caption file"):
        parse_captions("Dear diary, today I...")


def test_cues_to_plain_text_joins_with_speaker_prefix() -> None:
    cues = parse_webvtt(_VTT)
    plain = cues_to_plain_text(cues)
    assert plain == ("Margit: I remember the winter of 1974. It was the year everything changed.")


# --- model round-trip --------------------------------------------------------


def test_transcript_cue_is_immutable() -> None:
    cue = TranscriptCue(start="00:00:01.000", end="00:00:02.000", text="hi", speaker=None)
    with pytest.raises(AttributeError):
        cue.text = "changed"  # type: ignore[misc]


def test_payload_cues_round_trip_through_serialization() -> None:
    """A payload's structured cues survive serialize -> deserialize, same as transcript."""
    cues = (
        TranscriptCue(start="00:00:01.000", end="00:00:04.000", text="Hello.", speaker="A"),
        TranscriptCue(start="00:00:04.000", end="00:00:06.000", text="World.", speaker=None),
    )
    payload = PayloadFile(
        filename="clip.mp3",
        address=ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64),
        media_type="audio/mpeg",
        policy=AccessPolicy.PUBLIC,
        transcript=cues_to_plain_text(list(cues)),
        cues=cues,
    )
    record = Record(
        title="Clip",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Clip"]),
        payloads=[payload],
    )
    restored = deserialize_record(serialize_record(record))
    assert restored.payloads[0].cues == cues


def test_payload_with_no_cues_round_trips_to_empty_tuple() -> None:
    """A plain --transcript payload (no captions) still round-trips with cues == ()."""
    payload = PayloadFile(
        filename="clip.mp3",
        address=ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64),
        media_type="audio/mpeg",
        policy=AccessPolicy.PUBLIC,
        transcript="plain text only",
    )
    record = Record(title="Clip", payloads=[payload])
    restored = deserialize_record(serialize_record(record))
    assert restored.payloads[0].cues == ()
    assert restored.payloads[0].transcript == "plain text only"


# --- CLI end-to-end -----------------------------------------------------------


def _ingest_with_captions(
    tmp_path: Path, caption_text: str, *, filename: str = "clip.vtt"
) -> tuple[Archive, str]:
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "AV"]) == 0
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3 fake audio bytes")
    caption_file = tmp_path / filename
    caption_file.write_text(caption_text, encoding="utf-8")
    argv = [
        "ingest",
        "--root",
        str(root),
        "--title",
        "Recording",
        str(clip),
        "--captions",
        f"clip.mp3={caption_file}",
        "--actor",
        "s",
        "--now",
        "2026-01-01T00:00:00Z",
    ]
    assert cli.main(argv) == 0
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    return archive, rid


def test_cli_ingest_captions_stores_structured_cues(tmp_path: Path) -> None:
    archive, rid = _ingest_with_captions(tmp_path, _VTT)
    payload = archive.get(rid).payloads[0]
    assert len(payload.cues) == 2
    assert payload.cues[0].speaker == "Margit"
    assert payload.cues[0].start == "00:00:01.000"


def test_cli_ingest_captions_backfills_flat_transcript(tmp_path: Path) -> None:
    """No --transcript given: the flat field is auto-derived from the parsed cues,
    so every existing plain-text consumer (search, export, the H3 render) keeps
    working unchanged."""
    archive, rid = _ingest_with_captions(tmp_path, _VTT)
    payload = archive.get(rid).payloads[0]
    assert payload.transcript == cues_to_plain_text(list(payload.cues))
    assert "I remember the winter of 1974." in payload.transcript


def test_cli_ingest_srt_captions_also_works(tmp_path: Path) -> None:
    archive, rid = _ingest_with_captions(tmp_path, _SRT, filename="clip.srt")
    payload = archive.get(rid).payloads[0]
    assert len(payload.cues) == 2
    assert all(c.speaker is None for c in payload.cues)


def test_cli_ingest_no_accessibility_warning_when_captions_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Captions satisfy the existing WCAG 1.2 advisory (H3) just like --transcript
    does — the flat transcript they backfill is non-empty."""
    _ingest_with_captions(tmp_path, _VTT)
    err = capsys.readouterr().err
    assert "no transcript" not in err


def test_cli_ingest_rejects_malformed_captions_file(tmp_path: Path) -> None:
    """A malformed caption file fails the ingest with a clear, non-zero exit —
    never silently drops the captions or stores partial/garbage cues."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "AV"]) == 0
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3 fake audio bytes")
    bad = tmp_path / "bad.vtt"
    bad.write_text("not a caption file", encoding="utf-8")
    argv = [
        "ingest",
        "--root",
        str(root),
        "--title",
        "Recording",
        str(clip),
        "--captions",
        f"clip.mp3={bad}",
        "--actor",
        "s",
    ]
    assert cli.main(argv) != 0


def test_cli_ingest_explicit_transcript_overrides_caption_derived_text(tmp_path: Path) -> None:
    """An explicit --transcript for the same filename is kept verbatim; the cues
    still attach for the structured render, but the flat field is not overwritten."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "AV"]) == 0
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3 fake audio bytes")
    caption_file = tmp_path / "clip.vtt"
    caption_file.write_text(_VTT, encoding="utf-8")
    argv = [
        "ingest",
        "--root",
        str(root),
        "--title",
        "Recording",
        str(clip),
        "--transcript",
        "clip.mp3=A hand-written summary transcript.",
        "--captions",
        f"clip.mp3={caption_file}",
        "--actor",
        "s",
        "--now",
        "2026-01-01T00:00:00Z",
    ]
    assert cli.main(argv) == 0
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    payload = archive.get(rid).payloads[0]
    assert payload.transcript == "A hand-written summary transcript."
    assert len(payload.cues) == 2  # the structured cues still attach


# --- rendering ----------------------------------------------------------------


def test_render_shows_structured_cues_as_a_list(tmp_path: Path) -> None:
    archive, rid = _ingest_with_captions(tmp_path, _VTT)
    disclosed = archive.disclose(rid, anonymous(), now=_NOW)
    html = _record_main_html(disclosed, proceed=True)
    assert '<ol class="transcript-cues">' in html
    assert "00:00:01.000" in html
    assert "00:00:04.000" in html
    assert "Margit" in html
    assert "I remember the winter of 1974." in html


def test_render_falls_back_to_flat_paragraph_when_no_cues(tmp_path: Path) -> None:
    """The plain --transcript-only path (no captions) is unchanged: a flat <p>,
    not a list — pins that RM6 is additive, not a breaking change to H3."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "AV"]) == 0
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3 fake audio bytes")
    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Recording",
                str(clip),
                "--transcript",
                "clip.mp3=Plain transcript, no timing.",
                "--actor",
                "s",
                "--now",
                "2026-01-01T00:00:00Z",
            ]
        )
        == 0
    )
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    disclosed = archive.disclose(rid, anonymous(), now=_NOW)
    html = _record_main_html(disclosed, proceed=True)
    assert "<p>Plain transcript, no timing.</p>" in html
    assert "transcript-cues" not in html


# --- disclosure / consent gating ----------------------------------------------


def test_sealed_payload_hides_its_cues_too() -> None:
    """Cues carry no policy of their own: they are gated by, and travel with, the
    single PayloadFile policy — exactly like the flat transcript. Sealing the
    payload hides the structured segments from an anonymous grant along with
    everything else about it (RM6 does not add a second, weaker disclosure path)."""
    cues = (TranscriptCue(start="00:00:01.000", end="00:00:02.000", text="secret words"),)
    record = Record(
        title="Testimony",
        record_id="rec-testimony",
        default_policy=AccessPolicy.PUBLIC,
        payloads=[
            PayloadFile(
                filename="testimony.mp3",
                address=ContentAddress.parse("sha256:" + "d" * 64),
                policy=AccessPolicy.STEWARDS,
                transcript="secret words",
                cues=cues,
            )
        ],
    )
    public_view = disclose(record, anonymous(), _NOW)
    assert public_view.payloads == ()
    assert any(r.name == "testimony.mp3" for r in public_view.withheld)

    steward_view = disclose(record, steward("s1"), _NOW)
    assert len(steward_view.payloads) == 1
    assert steward_view.payloads[0].cues == cues


def test_tightening_payload_policy_hides_previously_public_cues(tmp_path: Path) -> None:
    """A steward tightening a payload's access (moderate.set_payload_policy) hides
    its structured cues from a now-unauthorized grant, exactly as it already does
    for the flat transcript and the file itself — the existing consent-tightening
    path needs no RM6-specific change because cues ride on the same policy."""
    archive, rid = _ingest_with_captions(tmp_path, _VTT)
    record = archive.get(rid)
    assert record.payloads[0].policy == AccessPolicy.PUBLIC  # ingest default

    tightened, event, _action = set_payload_policy(
        record,
        "clip.mp3",
        AccessPolicy.STEWARDS,
        reason="contributor asked to tighten access",
        actor="steward-1",
        now=_NOW,
    )
    archive.apply_update(tightened, event)

    public_view = archive.disclose(rid, anonymous(), now=_NOW)
    assert public_view.payloads == ()

    steward_view = archive.disclose(rid, steward("s1"), now=_NOW)
    assert len(steward_view.payloads[0].cues) == 2
