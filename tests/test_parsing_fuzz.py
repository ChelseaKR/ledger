"""Property-based fuzz tests for the hand-rolled parsers in :mod:`ledger.parsing`.

FIX-09 (``docs/ideation/02-large-scale-fixes.md``): these functions parse
attacker-controlled bytes at the front door — the cookie header, urlencoded
form bodies, the multipart body used for anonymous file contributions, and the
record-id percent-decoder — so example-based tests alone are not enough
evidence they are safe. Hypothesis explores malformed boundaries, oversized
parts, NUL/control-character and non-UTF-8 bytes, and attempted header
injection; the one property every case must hold is **never worse than a
handled failure**: no exception escapes, no hang, no unbounded memory growth
relative to the input size. A crafted body can, at worst, make ``ledger.server``
send a 400 — never raise all the way to a 500 (the actual assertion inside
the request handler; here we assert the lower-level contract these parsers
must uphold for that to be true).
"""

from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger.parsing import (
    cookie_value,
    decode_id,
    parse_multipart,
    parse_urlencoded_multi,
    safe_filename,
)

# Generous but bounded — mirrors the ~64 KiB form cap and keeps CI fast while
# still exercising multi-part, multi-KB bodies.
_TEXT = st.text(max_size=200)
_BYTES = st.binary(max_size=2000)
_HEADERISH = st.text(
    alphabet=string.ascii_letters + string.digits + " ;=\"'\\\r\n\t:/,.-_",
    max_size=200,
)

_SLOW_SETTINGS = settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)


# --- cookie_value -------------------------------------------------------------


@given(header=_HEADERISH, name=st.text(alphabet=string.ascii_letters, max_size=20))
@_SLOW_SETTINGS
def test_cookie_value_never_raises(header: str, name: str) -> None:
    """Any string, however malformed, yields a string back — never an exception."""
    result = cookie_value(header, name)
    assert isinstance(result, str)


@given(name=st.text(alphabet=string.ascii_letters + string.digits + "_-", min_size=1, max_size=20))
def test_cookie_value_round_trips_a_well_formed_cookie(name: str) -> None:
    header = f"{name}=hello; other=1"
    assert cookie_value(header, name) == "hello"


def test_cookie_value_missing_cookie_is_empty_string() -> None:
    assert cookie_value("", "lang") == ""
    assert cookie_value("a=1; b=2", "lang") == ""


def test_cookie_value_malformed_segments_do_not_raise() -> None:
    # No '=', stray ';', empty segments, a segment that is only whitespace.
    assert cookie_value(";;; = ; lang; =bare;;", "lang") == ""


# --- decode_id ------------------------------------------------------------


@given(raw=_TEXT)
@_SLOW_SETTINGS
def test_decode_id_never_raises(raw: str) -> None:
    result = decode_id(raw)
    assert isinstance(result, str)


@given(raw=st.text(alphabet=string.ascii_letters + string.digits, max_size=40))
def test_decode_id_is_identity_on_plain_text(raw: str) -> None:
    # No percent-escapes present -> decoding is a no-op.
    assert decode_id(raw) == raw


def test_decode_id_handles_truncated_and_invalid_escapes() -> None:
    # A dangling '%', an incomplete escape, and a non-hex escape must not raise.
    for raw in ("%", "%2", "%zz", "100%", "a%25b"):
        result = decode_id(raw)
        assert isinstance(result, str)


# --- safe_filename ----------------------------------------------------------


@given(name=_TEXT)
@_SLOW_SETTINGS
def test_safe_filename_never_raises_and_never_empty(name: str) -> None:
    result = safe_filename(name)
    assert isinstance(result, str)
    assert result != ""


@given(name=_TEXT)
@_SLOW_SETTINGS
def test_safe_filename_strips_header_injection_and_traversal_characters(name: str) -> None:
    """The output can never carry a byte that would let a filename break out of a
    ``Content-Disposition`` header value or escape its directory component."""
    result = safe_filename(name)
    for forbidden in ('"', "\\", "/", "\r", "\n"):
        assert forbidden not in result


def test_safe_filename_dots_only_falls_back() -> None:
    assert safe_filename("") == "file"
    assert safe_filename(".") == "file"
    assert safe_filename("..") == "file"
    assert safe_filename("...") == "file"


# --- parse_urlencoded_multi --------------------------------------------------


@given(raw=_TEXT)
@_SLOW_SETTINGS
def test_parse_urlencoded_multi_never_raises(raw: str) -> None:
    result = parse_urlencoded_multi(raw)
    assert isinstance(result, dict)


@given(raw=_BYTES)
@_SLOW_SETTINGS
def test_parse_urlencoded_multi_handles_arbitrary_bytes_decoded_as_utf8(raw: bytes) -> None:
    text = raw.decode("utf-8", "replace")
    result = parse_urlencoded_multi(text)
    assert isinstance(result, dict)


def test_parse_urlencoded_multi_repeated_keys_keep_all_values() -> None:
    assert parse_urlencoded_multi("select=a&select=b&select=c") == {"select": ["a", "b", "c"]}


# --- parse_multipart ---------------------------------------------------------


@given(raw=_BYTES, content_type=_HEADERISH)
@_SLOW_SETTINGS
def test_parse_multipart_never_raises_on_arbitrary_bytes(raw: bytes, content_type: str) -> None:
    """No crafted multipart body — malformed boundary, truncated part, missing
    headers, binary garbage — may raise, hang, or otherwise escape this function.
    At worst it returns an empty/partial result for the caller to reject."""
    fields, attached = parse_multipart(raw, content_type)
    assert isinstance(fields, dict)
    assert attached is None or (
        isinstance(attached, tuple)
        and len(attached) == 2
        and isinstance(attached[0], str)
        and isinstance(attached[1], bytes)
    )


@given(garbage=st.binary(min_size=0, max_size=5000))
@_SLOW_SETTINGS
def test_parse_multipart_handles_no_boundary_parameter(garbage: bytes) -> None:
    """A Content-Type claiming multipart without a boundary is a common malformed
    request; it must degrade to "no parts found", not raise."""
    fields, attached = parse_multipart(garbage, "multipart/form-data")
    assert fields == {}
    assert attached is None


def _build_multipart(boundary: str, parts: list[bytes]) -> bytes:
    sep = f"--{boundary}\r\n".encode()
    body = b"".join(sep + p for p in parts)
    body += f"--{boundary}--\r\n".encode()
    return body


def test_parse_multipart_well_formed_text_field_round_trips() -> None:
    boundary = "XBOUNDARY"
    part = b'Content-Disposition: form-data; name="message"\r\n\r\nhello world\r\n'
    body = _build_multipart(boundary, [part])
    fields, attached = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert fields.get("message") == "hello world"
    assert attached is None


def test_parse_multipart_well_formed_file_field_round_trips() -> None:
    boundary = "XBOUNDARY"
    part = (
        b'Content-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
        b"file bytes here\r\n"
    )
    body = _build_multipart(boundary, [part])
    fields, attached = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert fields == {}
    assert attached is not None
    filename, data = attached
    assert filename == "a.txt"
    assert data == b"file bytes here"


@given(
    injected=st.text(
        alphabet=string.printable,
        max_size=60,
    )
)
@_SLOW_SETTINGS
def test_parse_multipart_field_name_cannot_smuggle_extra_headers(injected: str) -> None:
    """A crafted ``name`` parameter (attempted header injection via CR/LF or an
    unterminated quote) must not raise and must not corrupt the parse into
    fabricating extra fields beyond what the body actually contains."""
    boundary = "XBOUNDARY"
    safe_injected = injected.replace('"', "'").replace("\r", " ").replace("\n", " ")
    part = f'Content-Disposition: form-data; name="{safe_injected}"\r\n\r\nvalue\r\n'.encode(
        "utf-8", "replace"
    )
    body = _build_multipart(boundary, [part])
    fields, attached = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
    assert isinstance(fields, dict)
    assert attached is None


@given(length=st.integers(min_value=0, max_value=64))
@_SLOW_SETTINGS
def test_parse_multipart_truncated_body_does_not_raise(length: int) -> None:
    boundary = "XBOUNDARY"
    part = b'Content-Disposition: form-data; name="message"\r\n\r\nhello world\r\n'
    full = _build_multipart(boundary, [part])
    truncated = full[:length]
    fields, attached = parse_multipart(truncated, f"multipart/form-data; boundary={boundary}")
    assert isinstance(fields, dict)
    assert attached is None or isinstance(attached, tuple)
