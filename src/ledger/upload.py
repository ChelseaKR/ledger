"""Safe validation of an uploaded binary payload (backlog A2).

The contribution form lets a contributor attach a file (audio, image, PDF). A web
upload is an attack surface, so nothing about the client is trusted: not the
declared content-type, not the filename, not the extension. The bytes themselves
decide. This module bounds the size and **sniffs the leading magic bytes** against a
small allowlist, returning the *server-determined* media type or rejecting the file
outright.

Design:

* **Allowlist, not blocklist.** Only the handful of formats below are accepted; an
  unrecognised or disallowed file is refused, never stored "just in case".
* **Magic bytes, not metadata.** The type is read from the content, so a `.pdf`
  extension on an executable, or a forged ``Content-Type``, cannot smuggle a type in.
* **Bounded.** ``MAX_UPLOAD_BYTES`` caps the size before anything is stored, so an
  upload cannot exhaust memory or disk (availability).

No-outing: this module sees only opaque bytes and a (distrusted) filename; it never
touches a contributor identity or a sealed value.
"""

from __future__ import annotations

# 25 MiB. Big enough for a scanned zine page, an image, or a short audio clip on the
# single inexpensive box ledger targets; small enough that one upload cannot exhaust
# it. A steward fronting the server can lower the effective limit at the proxy.
MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

# media type -> the leading byte signatures that identify it. Kept deliberately tiny
# and well-known; extend only with formats a community actually needs.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),  # plus "WEBP" at offset 8, checked below
    "application/pdf": (b"%PDF-",),
    "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    "audio/ogg": (b"OggS",),
    "audio/wav": (b"RIFF",),  # plus "WAVE" at offset 8, checked below
}

#: The media types this build will accept, for showing the contributor.
ALLOWED_TYPES: tuple[str, ...] = tuple(sorted(_SIGNATURES))


def sniff_media_type(data: bytes) -> str | None:
    """Return the allowlisted media type ``data`` actually is, or ``None`` to reject.

    The decision is made purely from the leading bytes, so a forged filename or
    ``Content-Type`` cannot influence it. ``RIFF`` containers (WebP, WAV) are
    disambiguated by the four bytes at offset 8 so they are not confused with each
    other or with any other RIFF type.
    """
    for media_type, signatures in _SIGNATURES.items():
        # Keep the public allowlist and the recognizer tied together. Besides
        # guarding future edits, this makes ALLOWED_TYPES an exercised contract in
        # this module rather than an export that static analysis sees as orphaned.
        if media_type not in ALLOWED_TYPES:
            continue
        for signature in signatures:
            if data.startswith(signature):
                if media_type == "image/webp" and not (len(data) >= 12 and data[8:12] == b"WEBP"):
                    continue
                if media_type == "audio/wav" and not (len(data) >= 12 and data[8:12] == b"WAVE"):
                    continue
                return media_type
    return None
