"""WebVTT and SRT caption/transcript parsing (RM6).

RM6 ("captions/transcripts as a first-class ingest step") was scoped, on review,
to the part that is real, bounded, and standards-grounded: **ingesting an
already-transcribed caption file** a contributor or steward uploads — a WebVTT or
SRT track produced by a human transcriber, a captioning vendor, or an editing
tool. ledger does no speech-to-text and never will as part of this module; the
real transcription work and the harder UX/consent decisions around it stay
deferred to a human, exactly as the roadmap says. What *is* concrete enough to
build is parsing the two real, standard segment/timing formats such a file
actually arrives in:

* **WebVTT** — the W3C "Web Video Text Tracks Format",
  https://www.w3.org/TR/webvtt1/. A WebVTT file is a ``WEBVTT`` signature
  followed by a blank-line-separated sequence of **cues**, each an optional cue
  identifier line, a *cue timings* line (``start --> end``, optionally followed
  by cue settings), and one or more lines of cue payload text. A timestamp is
  ``[hours ':'] MM ':' SS '.' mmm`` — minutes/seconds always two digits,
  milliseconds always three, hours required only when non-zero (spec: "Two or
  more ASCII digits, representing the hours ... Two ASCII digits, representing
  the minutes ... Two ASCII digits, representing the seconds ... Three ASCII
  digits, representing the thousandths of a second"). The end timestamp must be
  greater than the start. A ``NOTE`` block ("start with the word 'NOTE' ...
  ignored by the parser") and (rarer) ``STYLE``/``REGION`` blocks are comments,
  not cues, and are skipped. Speaker attribution has a real, standardized
  mechanism: the **voice span**, ``<v Speaker Name>text`` (spec: "cue span start
  tag 'v' that requires an annotation; the annotation represents the name of the
  voice"), optionally closed with ``</v>`` or left open when it covers the whole
  cue.

* **SRT (SubRip)** — there is **no formal specification body or RFC**; it is a
  de facto format fixed by the original SubRip application and now near-universal
  by convention. Every block is a sequence number, a timings line
  ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` (hours always two digits, comma — not dot —
  before the milliseconds), one or more lines of subtitle text, and a blank line.
  SRT has **no standardized speaker-attribution syntax** — unlike WebVTT's ``<v>``
  voice span, nothing in the de facto format names who is speaking, so an
  SRT-derived cue always carries ``speaker=None`` here. Inventing an informal
  "Name:" convention and parsing it as if it were part of the format would be
  fabricating a spec that does not exist; this module does not do that.

WCAG's own captioning guidance (2.2 Success Criterion 1.2.2 Captions
(Prerecorded), Level A — https://www.w3.org/WAI/WCAG22/Understanding/captions-prerecorded.html)
requires that captions be "a synchronized visual and/or text alternative for both
speech and non-speech audio information" and, per the Understanding doc, that they
"identify who is speaking" and "convey ... equivalents for non-dialogue audio
information ... including sound effects, music, laughter, speaker identification
and location". Neither WCAG nor its Understanding document prescribes a maximum
segment length, a minimum cue duration, or millisecond-level synchronization
tolerances — synchronization is required, but no specific granularity is
mandated. That is why this module's segment/timing model is exactly what WebVTT
and SRT themselves define (one cue = one timed unit with start, end, and text),
rather than an invented reading-speed or line-length policy WCAG does not actually
require.

Design, consistent with the rest of ledger:

* **Sniff by content, not filename** (mirrors :mod:`ledger.upload`'s "magic bytes,
  not metadata" rule for uploaded binaries): :func:`sniff_caption_format` looks at
  the actual text — the ``WEBVTT`` signature, or a sequence-number-then-timings
  shape — never a ``.vtt``/``.srt`` extension a client could get wrong or forge.
* **Fail closed on malformed input.** A missing signature, an unparsable
  timestamp, an end time not after its start, or a block with no timings line
  raises :class:`~ledger.errors.CaptionParseError` naming only the cue index and
  the condition — never the caption text itself, which is contributor-supplied
  prose that can be exactly as sensitive as the transcript it is captioning
  (no-outing-adjacent: defense in depth on error messages, as everywhere in
  ledger).
* **Pure and dependency-free.** No I/O, no clock, no network; a function of the
  text given. Determinism: the same input always yields the same cue list.
* **No policy here.** This module knows nothing about :class:`~ledger.models.AccessPolicy`
  or consent — parsing is upstream of disclosure. See
  :class:`~ledger.models.TranscriptCue` and :class:`~ledger.models.PayloadFile`
  for how a parsed cue list is gated (today: the same single policy as the
  payload it transcribes, same as the existing flat ``transcript`` field) and for
  the open per-cue-consent-granularity question this module deliberately leaves
  unanswered.
"""

from __future__ import annotations

import re

from ledger.errors import CaptionParseError
from ledger.models import TranscriptCue

__all__ = [
    "cues_to_plain_text",
    "parse_captions",
    "parse_srt",
    "parse_webvtt",
    "sniff_caption_format",
]

# WebVTT cue span start tag for a voice: <v Speaker Name> or <v.loud Speaker Name>
# (an optional dot-separated class list before the annotation). The annotation —
# everything up to the closing '>' — is the voice name (WebVTT spec §"WebVTT cue
# voice span").
_VOICE_TAG = re.compile(r"<v(?:\.[^>.\s]+)*[ \t]+([^>]+)>")

# Any WebVTT cue-span tag: <v ...>, </v>, <b>, <i>, <u>, <c>, <lang ...>, or a
# cue-internal timestamp tag like <00:00:01.000>. Stripped from cue payload text
# after the voice (if any) has been pulled out, so the stored text is plain prose,
# not markup.
_ANY_TAG = re.compile(r"<[^>]*>")

# WebVTT timestamp (spec: hours optional-but-required-if-nonzero, minutes/seconds
# always 2 digits 00-59, milliseconds always 3 digits, '.' before the milliseconds).
_WEBVTT_TS = re.compile(r"^(?:(\d{2,}):)?([0-5]\d):([0-5]\d)\.(\d{3})$")

# SRT timestamp: hours always 2+ digits, ',' (not '.') before the milliseconds.
_SRT_TS = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$")

# A blank line (allowing trailing whitespace) separates WebVTT/SRT blocks in both
# formats.
_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n+")


def _normalize_newlines(text: str) -> str:
    """Strip a leading BOM and normalize CRLF/CR to LF, as both formats require."""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _has_webvtt_signature(text: str) -> bool:
    return text == "WEBVTT" or text.startswith(("WEBVTT\n", "WEBVTT ", "WEBVTT\t"))


def _is_webvtt_metadata_block(head: str) -> bool:
    return head in {"NOTE", "STYLE", "REGION"} or head.startswith(
        ("NOTE ", "NOTE\t", "STYLE ", "STYLE\t", "REGION ", "REGION\t")
    )


def _parse_timestamp(raw: str, pattern: re.Pattern[str], *, context: str) -> float:
    """Parse one WebVTT- or SRT-shaped timestamp to seconds, or raise naming ``context``."""
    match = pattern.match(raw.strip())
    if not match:
        raise CaptionParseError(f"{context}: unparsable timestamp")
    hours = int(match.group(1)) if match.group(1) else 0
    minutes, seconds, millis = int(match.group(2)), int(match.group(3)), int(match.group(4))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _format_timestamp(total_seconds: float) -> str:
    """Render seconds as ``HH:MM:SS.mmm`` — the normalized form every cue carries.

    Always zero-pads hours to at least two digits (WebVTT permits omitting a
    zero hour; ledger always includes it) so every :class:`~ledger.models.TranscriptCue`
    has one predictable shape regardless of source format (correctness,
    interchangeability).
    """
    total_millis = round(total_seconds * 1000)
    hours, remainder = divmod(total_millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _split_timing_line(line: str, *, context: str) -> tuple[str, str]:
    """Split a ``start --> end [settings]`` line into its two timestamp tokens."""
    if "-->" not in line:
        raise CaptionParseError(f"{context}: cue has no timings line")
    start_raw, _, rest = line.partition("-->")
    # Cue settings (WebVTT) trail the end timestamp separated by whitespace; SRT
    # timing lines rarely carry anything after the end time, but tolerate it the
    # same way.
    end_raw = rest.strip().split(None, 1)[0] if rest.strip() else ""
    return start_raw.strip(), end_raw


def _extract_voice(payload_lines: list[str]) -> tuple[str | None, str]:
    """Pull a WebVTT voice-span speaker (if any) and the plain text out of a cue payload.

    Only the *first* voice span's annotation is captured as ``speaker`` — a cue
    with multiple interleaved voice spans (rare; simultaneous/overlapping speech)
    still yields one flattened text with every span's words in order, but only
    the first speaker is named. Every cue-span tag (``<v>``, ``<b>``, ``<i>``,
    ``<c>``, a cue-internal timestamp tag, etc.) is stripped so the stored text is
    plain prose.
    """
    raw = "\n".join(payload_lines).strip()
    match = _VOICE_TAG.search(raw)
    speaker = match.group(1).strip() if match else None
    cleaned = _ANY_TAG.sub("", raw)
    text = " ".join(cleaned.split())
    return speaker, text


def sniff_caption_format(text: str) -> str | None:
    """Return ``"vtt"``, ``"srt"``, or ``None`` — decided from content, not a filename.

    Mirrors :func:`ledger.upload.sniff_media_type`'s "the bytes decide, not the
    declared type" rule: a WebVTT file is recognised by its mandatory ``WEBVTT``
    signature (optionally after a BOM); an SRT file is recognised by its
    characteristic shape — a leading sequence-number line followed by a
    ``-->`` timings line, or (tolerating the sequence number some tools omit) a
    timings line using SRT's comma millisecond separator as the very first
    non-blank line. Anything else is unrecognised.
    """
    body = _normalize_newlines(text).lstrip()
    if _has_webvtt_signature(body):
        return "vtt"
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) >= 2 and lines[0].strip().isdigit() and "-->" in lines[1]:
        return "srt"
    if "-->" in lines[0] and "," in lines[0]:
        return "srt"
    return None


def _parse_webvtt_cue(lines: list[str], cue_number: int) -> TranscriptCue:
    context = f"cue {cue_number}"
    timing_index = 0 if "-->" in lines[0] else 1
    if timing_index >= len(lines):
        raise CaptionParseError(f"{context}: cue has no timings line")
    start_raw, end_raw = _split_timing_line(lines[timing_index], context=context)
    start = _parse_timestamp(start_raw, _WEBVTT_TS, context=f"{context} start time")
    end = _parse_timestamp(end_raw, _WEBVTT_TS, context=f"{context} end time")
    if end <= start:
        raise CaptionParseError(f"{context}: end time must be after the start time")
    speaker, cue_text = _extract_voice(lines[timing_index + 1 :])
    if not cue_text:
        raise CaptionParseError(f"{context}: cue text must not be empty")
    return TranscriptCue(
        start=_format_timestamp(start),
        end=_format_timestamp(end),
        text=cue_text,
        speaker=speaker,
    )


def parse_webvtt(text: str) -> list[TranscriptCue]:
    """Parse a WebVTT file's text into an ordered list of :class:`TranscriptCue`.

    Implements the cue grammar from https://www.w3.org/TR/webvtt1/: a mandatory
    ``WEBVTT`` signature block, then cues separated by one or more blank lines,
    each cue an optional identifier line, a ``start --> end`` timings line (cue
    settings after the end time are accepted and discarded — ledger has no
    rendering surface for VTT positioning), and one or more text lines. ``NOTE``,
    ``STYLE``, and ``REGION`` blocks are comments/configuration, not cues, and are
    skipped exactly as the spec requires ("ignored by the parser"). Raises
    :class:`~ledger.errors.CaptionParseError` (naming only the cue index and
    condition, never the text) on a missing signature, a missing or unparsable
    timings line, a malformed timestamp, an end time not after its start, or a
    file with no cues at all.
    """
    body = _normalize_newlines(text)
    signature = body.lstrip()
    if not _has_webvtt_signature(signature):
        raise CaptionParseError("not a WebVTT file: missing the 'WEBVTT' signature")
    blocks = _BLOCK_SPLIT.split(body.strip("\n"))

    cues: list[TranscriptCue] = []
    for block_index, block in enumerate(blocks):
        lines = block.split("\n")
        if block_index == 0:
            # The header block: the WEBVTT signature plus any optional free text
            # up to the first blank line. Never a cue.
            continue
        if not lines or not lines[0].strip():
            continue
        head = lines[0].strip()
        if _is_webvtt_metadata_block(head):
            continue

        cues.append(_parse_webvtt_cue(lines, len(cues) + 1))

    if not cues:
        raise CaptionParseError("no cues found in WebVTT file")
    return cues


def parse_srt(text: str) -> list[TranscriptCue]:
    """Parse an SRT (SubRip) file's text into an ordered list of :class:`TranscriptCue`.

    SRT has no formal specification; this implements the near-universal de facto
    shape: blocks separated by a blank line, each an optional sequence-number
    line, a ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` timings line, and one or more text
    lines. The sequence number is tolerated but not required or checked for
    order — real-world SRT files are inconsistent about it and nothing here
    depends on its value. Every cue's ``speaker`` is ``None`` (see the module
    docstring: SRT has no standardized speaker syntax). Raises
    :class:`~ledger.errors.CaptionParseError` (naming only the cue index and
    condition, never the text) on a missing or unparsable timings line, an end
    time not after its start, or a file with no cues at all.
    """
    body = _normalize_newlines(text).strip("\n")
    if not body.strip():
        raise CaptionParseError("empty SRT file")
    blocks = [b for b in _BLOCK_SPLIT.split(body) if b.strip()]

    cues: list[TranscriptCue] = []
    for block in blocks:
        lines = block.split("\n")
        idx = 0
        if lines[idx].strip().isdigit():
            idx += 1  # the conventional sequence-number line
        if idx >= len(lines):
            raise CaptionParseError(f"cue {len(cues) + 1}: cue has no timings line")
        context = f"cue {len(cues) + 1}"
        start_raw, end_raw = _split_timing_line(lines[idx], context=context)
        start = _parse_timestamp(start_raw, _SRT_TS, context=f"{context} start time")
        end = _parse_timestamp(end_raw, _SRT_TS, context=f"{context} end time")
        if end <= start:
            raise CaptionParseError(f"{context}: end time must be after the start time")
        text_lines = lines[idx + 1 :]
        cue_text = " ".join(" ".join(text_lines).split())
        if not cue_text:
            raise CaptionParseError(f"{context}: cue text must not be empty")
        cues.append(
            TranscriptCue(
                start=_format_timestamp(start),
                end=_format_timestamp(end),
                text=cue_text,
                speaker=None,
            )
        )

    if not cues:
        raise CaptionParseError("no cues found in SRT file")
    return cues


def parse_captions(text: str) -> list[TranscriptCue]:
    """Sniff ``text``'s format and parse it, or raise if neither format is recognised.

    The one entry point a caller (the ``ingest`` CLI, a future contribute-form
    upload) needs: format detection and parsing in one step, content-sniffed per
    :func:`sniff_caption_format` rather than trusting a filename extension.
    """
    fmt = sniff_caption_format(text)
    if fmt == "vtt":
        return parse_webvtt(text)
    if fmt == "srt":
        return parse_srt(text)
    raise CaptionParseError(
        "not a recognised caption file: expected a WebVTT ('WEBVTT' signature) "
        "or SRT (numbered, timed blocks) file"
    )


def cues_to_plain_text(cues: list[TranscriptCue]) -> str:
    """Flatten ``cues`` to one plain-text transcript, speaker-prefixed where known.

    This is what backfills the existing flat :attr:`~ledger.models.PayloadFile.transcript`
    field when captions are ingested, so every existing plain-text consumer
    (search indexing, the non-structured transcript render, an export) keeps
    working unchanged — RM6 adds structure, it does not replace the flat field.
    """
    parts: list[str] = []
    for cue in cues:
        parts.append(f"{cue.speaker}: {cue.text}" if cue.speaker else cue.text)
    return " ".join(p for p in parts if p)
