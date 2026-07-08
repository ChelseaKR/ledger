"""Pure, hand-rolled request-body and header parsers used by :mod:`ledger.server`.

Pulled out of the request handler (FIX-09, ``docs/ideation/02-large-scale-fixes.md``)
so the parsing logic that guards the front door — a cookie header, a urlencoded
form body, a hand-written ``multipart/form-data`` reader, and the record-id
percent-decoder — is a small, dependency-free, independently importable and
independently *fuzzable* unit, separate from the ~2,000-line HTTP handler that
used to own it.

Every function here is pure: it takes bytes/str already read off the wire (the
handler still owns bounding the read by ``Content-Length`` and any size caps —
that is I/O policy, not parsing) and returns a plain data structure. None of them
raise on malformed input; a hostile or truncated body degrades to an empty/partial
result rather than a 500, a hang, or an over-read (availability, no-outing-safe
error handling). See ``tests/test_parsing_fuzz.py`` for the Hypothesis property
tests that exercise exactly this contract: malformed multipart boundaries,
oversized parts, NUL/UTF-8 edge cases, and attempted header injection.
"""

from __future__ import annotations

from email import policy as email_policy
from email.parser import BytesParser
from urllib.parse import parse_qs, unquote


def cookie_value(cookie_header: str, name: str) -> str:
    """Return the value of cookie ``name`` from a raw ``Cookie`` header, or ``""``.

    A small, dependency-free parse; ledger only ever reads a UI-language
    preference this way, and that cookie carries no identity (no-outing rule).
    Malformed segments (no ``=``, stray ``;``, empty names) are simply skipped
    rather than raising.
    """
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


def decode_id(raw: str) -> str:
    """Percent-decode a single path segment for matching against record ids.

    Path traversal is handled separately by the static-file handler; for record
    ids the decoded value is only ever used as a dictionary/file lookup key by
    the disclosure-gated archive, never interpolated into a path here.
    """
    return unquote(raw)


def safe_filename(name: str) -> str:
    """A filename safe to place in a ``Content-Disposition`` header.

    Strips path separators, quotes, and control characters so a crafted payload
    filename cannot inject a header or escape the field (securability). A name
    that survives as empty or as only dots (``.``/``..``) is replaced with a
    safe default: as a path component such a name points at a directory, so
    writing the upload to ``tmpdir / name`` would raise instead of storing a
    file (robustness).
    """
    cleaned = "".join(c for c in name if c.isprintable() and c not in '"\\/\r\n')
    if not cleaned.strip("."):
        return "file"
    return cleaned


def parse_urlencoded_multi(raw: str) -> dict[str, list[str]]:
    """Parse a urlencoded body into a mapping keeping *all* values per key.

    Thin, named wrapper over :func:`urllib.parse.parse_qs` so callers (and
    fuzzers) have one place to reason about form decoding regardless of where
    the raw bytes came from.
    """
    return parse_qs(raw)


def parse_multipart(
    raw: bytes, content_type: str
) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    """Parse a ``multipart/form-data`` body into ``(fields, upload)``.

    Parses with the stdlib email parser by prepending the request's
    ``Content-Type`` as a MIME header — no third-party multipart library. Each
    text part becomes a ``fields`` entry; the first part carrying a filename
    becomes the single ``upload``. The filename is kept only to suggest a
    stored name and is sanitised elsewhere (:func:`safe_filename`); the bytes
    are never trusted on type until sniffed by the caller.

    Bounding the size of ``raw`` before calling this is the caller's job (it is
    I/O policy); this function only ever reads the bytes it is given and never
    performs unbounded work relative to their length. A missing boundary,
    truncated part, absent ``name``/``filename`` parameter, or a part whose
    payload fails to decode all degrade gracefully — an empty or partial
    result — rather than raising.
    """
    header = (
        b"Content-Type: "
        + content_type.encode("latin-1", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
    )
    message = BytesParser(policy=email_policy.default).parsebytes(header + raw)
    fields: dict[str, str] = {}
    attached: tuple[str, bytes] | None = None
    if message.is_multipart():
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name is None:
                continue
            decoded = part.get_payload(decode=True)
            data = decoded if isinstance(decoded, bytes) else b""
            filename = part.get_filename()
            if filename:
                if attached is None and data:
                    attached = (filename, data)
            else:
                fields[str(name)] = data.decode("utf-8", "replace")
    return fields, attached
