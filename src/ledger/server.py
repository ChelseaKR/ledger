"""The accessible, framework-free browse/search server — ledger's public face.

This is the only surface most people ever touch, so two qualities dominate every
line: **accessibility** (WCAG 2.2 AA) and **safety** (the no-outing rule).

Framework-free, standard-library only (:mod:`http.server`) -> portability,
affordability, no lock-in: a community runs the whole public site on one
inexpensive box with no web framework, no build step, and no paid service.

Every record that reaches a response is produced by :meth:`Archive.browse` or
:meth:`Archive.disclose`, i.e. by :func:`ledger.access.disclose` — the single
disclosure chokepoint. There is **no** code path here that constructs a record
view from anything but a :class:`~ledger.models.DisclosedRecord`, and a
``DisclosedRecord`` structurally cannot carry a contributor identity: it has no
``identity_ref`` field. Consequently no route, query parameter, response header,
JSON field, HTML attribute, log line, health summary, or error page in this
module can ever expose ``identity_ref`` or any contributor identity (safety,
confidentiality — the no-outing rule, confirmed in depth below):

* Reads go only through ``disclose``/``browse`` (the safe shape).
* The request log is overridden to emit a method + status + scrubbed path only —
  never headers, never a grant subject, never a query string echo of identity.
* Every interpolated string is passed through :func:`html.escape` (and JSON is
  serialized with the standard encoder), so untrusted text cannot break the page
  structure or inject script (security — no XSS) and renders correctly.
* The static handler resolves and bounds every path under ``web/static`` so a
  ``../`` cannot escape the document root (securability — no path traversal).

Grant resolution is deny-by-default and *authenticated*: requests are anonymous
unless the ``X-Ledger-Grant`` header carries a valid HMAC-signed capability token
(``subject:expiry:mac`` under ``LEDGER_GRANT_SECRET``) that mints a subject which
is (a) not on the revocation list and (b) pre-provisioned in the grants file. A
missing header, a missing secret, a forged/expired token, a revoked subject, or an
unprovisioned subject all fall back to the same anonymous grant, so the header is
never trusted beyond authenticating a lookup into an existing grant, and a bearer
token by itself confers nothing (least privilege, securability). Each honoured
grant use is recorded in a scrubbed audit line — subject and route class only,
never the token (no-outing rule).
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import http.server
import json
import os
import re
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, quote, urlsplit

from ledger import consent, contribute, export, i18n, oai, pagination, review, search, upload
from ledger.access import anonymous, disclose, is_listable
from ledger.access.grants import load_grants, load_revocations, verify_grant_token
from ledger.errors import (
    AccessDenied,
    LedgerError,
    ModerationError,
    ObjectNotFound,
    ValidationError,
)
from ledger.fixity import CHUNK_SIZE
from ledger.ingest import Archive
from ledger.lockdown import is_locked_down
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DisclosedRecord,
    Grant,
    HashAlgo,
    PayloadFile,
    PremisEvent,
    PremisEventType,
    Record,
    now_iso,
)
from ledger.moderate import add_content_warning, change_consent, execute_takedown, takedown
from ledger.parsing import cookie_value, parse_multipart, parse_urlencoded_multi
from ledger.parsing import decode_id as _decode_id
from ledger.parsing import safe_filename as _safe_filename
from ledger.render import (
    _browse_main_html,
    _error_main_html,
    _esc,
    _history_main_html,
    _is_insider,
    _nav_html,
    _overview_main_html,
    _page,
    _record_main_html,
)
from ledger.tombstones import PRIMARY_LOCATION, TombstoneStore

# Where the bundled, framework-free web assets live, resolved relative to this
# module so the server works from any working directory (portability). The static
# root is the canonical boundary the traversal guard enforces.
_WEB_ROOT: Path = Path(__file__).resolve().parent.parent.parent / "web"
_STATIC_ROOT: Path = (_WEB_ROOT / "static").resolve()

# Header carrying an HMAC-signed capability token (``subject:expiry:mac``). The
# server verifies the token under ``LEDGER_GRANT_SECRET`` and uses the authenticated
# subject only as a *lookup key* into a pre-provisioned grants file; an unsigned or
# forged header confers nothing (deny by default, least privilege).
_GRANT_HEADER: str = "X-Ledger-Grant"

# Serializes the grant-use audit log's read-modify-write. `log_grant_use` reads the
# whole PREMIS log and writes it back; under the threaded server two concurrent
# privileged requests could interleave and *lose* an audit line (the on-disk write
# is atomic, which prevents corruption but not lost updates). A process-wide lock
# is correct here — the log is per-archive but appends are quick, and a lock is a
# synchronization primitive, not a per-archive dependency, so it does not violate
# the attach-dependencies-to-the-server doctrine below.
_GRANT_LOG_LOCK = threading.Lock()

# A conservative allowlist of static file suffixes to content types. Anything not
# listed is served as ``application/octet-stream`` rather than guessed, so the
# server never advertises an executable or active type it did not intend to
# (securability). Kept tiny on purpose — this site ships only CSS.
_STATIC_CONTENT_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _load_static_files() -> dict[str, Path]:
    """Build the served-static allowlist once, at import: name -> real path.

    Only regular files directly under the canonical static root, with a known
    suffix, are eligible. Requests then match by *name* against this map, so a
    request value never becomes part of a filesystem path (no traversal is even
    expressible). A missing static dir yields an empty map (every static request
    404s) rather than an import-time crash (robustness)."""
    if not _STATIC_ROOT.is_dir():
        return {}
    return {
        entry.name: entry
        for entry in _STATIC_ROOT.iterdir()
        if entry.is_file() and entry.suffix.lower() in _STATIC_CONTENT_TYPES
    }


# Name -> path for every servable file under ``web/static``. Built at import so a
# request value is only ever a dict key, never interpolated into a filesystem path.
_STATIC_FILES: dict[str, Path] = _load_static_files()

# Friendly labels for consent/objection request kinds, shared by the steward console
# and the contributor status page so a steward can tell a subject's objection from a
# contributor's own request at a glance (user research B3).
# --- the request handler ----------------------------------------------------


class ArchiveRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve the accessible browse/search site over the standard-library server.

    The handler is read-only and disclosure-gated: every record-bearing response
    is built from a :class:`~ledger.models.DisclosedRecord` produced by
    :meth:`Archive.browse`/:meth:`Archive.disclose`, so no route can emit a
    contributor identity or an ``identity_ref`` (safety — the no-outing rule).

    Instances are created per request by :class:`http.server.HTTPServer`; the
    :class:`Archive` and grants mapping are bound once on the server object (see
    :func:`make_server`) and read here, so no per-request wiring is needed
    (simplicity).
    """

    # Bound onto the server in `make_server`; mirrored here for type-checked access.
    # A generic Server header with no version, and an empty sys_version, so the
    # response does not advertise the exact runtime to an attacker (user research
    # P2-2: suppress the version side-channel).
    server_version = "ledger"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- safety: a scrubbed access log -------------------------------------

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Emit a minimal, identity-free access line with the *real* status.

        The default would log ``self.requestline`` verbatim — which includes the
        query string (e.g. ``/search?q=...``) and so could record a search term.
        We log only the method, the query-stripped path, and the response ``code``
        the framework passes in, so a grant subject or search term never reaches the
        log (no-outing rule — logs disclose nothing).
        """
        path = urlsplit(self.path).path
        super().log_message("%s %s %s", self.command or "?", path, code)

    def log_error(self, format: str, *args: object) -> None:
        """Log errors without echoing any request data.

        Error logging never formats the (possibly sensitive) request line or args;
        it records a fixed, scrubbed line keyed on the query-stripped path only.
        """
        super().log_message("error handling %s", urlsplit(self.path).path)

    # --- grant resolution (deny by default) --------------------------------

    def _resolve_grant(self) -> Grant:
        """Resolve the viewer's grant — anonymous unless an *authenticated* subject is named.

        The ``X-Ledger-Grant`` header is no longer a bare subject string: it carries
        an HMAC-signed capability token minting a subject and an expiry. Resolution
        is deny-by-default at every step, and any failure collapses to the same
        anonymous public grant so an attacker learns nothing from the difference:

        * no header, or no server grant secret configured -> anonymous;
        * a forged, malformed, or expired token (:func:`verify_grant_token` returns
          ``None``) -> anonymous;
        * a subject on the revocation list -> anonymous, even with a still-valid MAC
          (immediate retraction without rotating the secret) — and a revocation list
          that exists but cannot be read counts as revoking *everyone*, because
          silently un-revoking on a corrupt file would be a fail-open (fail closed);
        * a subject with no pre-provisioned grant -> anonymous (a valid token authors
          nothing by itself; the grant is what a steward provisioned ahead of time).

        Only when a token authenticates a subject that is *not* revoked *and* has a
        provisioned grant is that grant returned — and a scrubbed grant-use line is
        appended to the archive audit log first (subject + route class only, never
        the token) so privileged access is accountable (least privilege, no-outing
        rule).

        Duress override (EXP-02): while the archive is in **lockdown**, every request
        is forced down to the anonymous public grant regardless of the header, so no
        route can disclose community-, steward-, or sealed-tier material — the whole
        surface fails closed to PUBLIC-only until a steward stands the archive back up
        (fail-closed, the no-outing rule under coercion). Read paths still serve the
        public face; every ``is_steward``-gated write is refused as a side effect.
        """
        if is_locked_down(self._archive()):
            return anonymous()
        token = self.headers.get(_GRANT_HEADER)
        if token is None:
            return anonymous()
        subject = verify_grant_token(token, self._grant_secret(), now=now_iso())
        if subject is None:
            return anonymous()
        revocations = self._revocations()
        if revocations is None or subject in revocations:
            return anonymous()
        grant = self._grants().get(subject)
        if grant is None:
            return anonymous()
        # A grant-use audit write must never fail the read it is recording; the lock
        # serializes the log's read-modify-write so concurrent privileged requests
        # on the threaded server never interleave and lose an audit line.
        with contextlib.suppress(OSError, LedgerError), _GRANT_LOG_LOCK:
            self._archive().log_grant_use(subject, self._route_class())
        return grant

    def _route_class(self) -> str:
        """A coarse, identity-free class for the current route (for the grant-use log).

        Only the class of surface is recorded — ``api``, ``steward``, ``static``, or
        ``browse`` — never the concrete path (which could carry a record id) or the
        query string (which could carry a search term), so the audit line discloses
        nothing (no-outing rule)."""
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            return "api"
        if path.startswith("/steward"):
            return "steward"
        if path.startswith("/static/"):
            return "static"
        return "browse"

    # --- typed access to the server-bound dependencies ---------------------

    def _archive(self) -> Archive:
        archive = getattr(self.server, "archive", None)
        if not isinstance(archive, Archive):  # pragma: no cover - misconfiguration
            raise LedgerError("server is not bound to an Archive")
        return archive

    def _grants(self) -> dict[str, Grant]:
        grants = getattr(self.server, "grants", None)
        return grants if isinstance(grants, dict) else {}

    def _grant_secret(self) -> bytes:
        """The configured grant-token secret as bytes, or empty when none is set.

        Mirrors :meth:`_claim_secret`: the secret lives only in the environment
        (``LEDGER_GRANT_SECRET``), never in argv or a file the archive serves, and an
        unset secret means *every* token is anonymous — no accidental trust when a
        deployment forgot to configure one (deny by default, fail closed)."""
        return os.environ.get("LEDGER_GRANT_SECRET", "").encode("utf-8")

    def _revocations(self) -> set[str] | None:
        """The current revoked-subject set, re-read from disk on every call.

        Re-reading (rather than caching a set at startup) is what makes
        ``ledger grant revoke`` an *immediate* retraction: the very next
        authenticated request consults the updated file, with no server restart.
        The file is tiny and only authenticated requests reach this point, so the
        re-read costs one small file open on exactly the requests that must be
        checked. A missing file is the empty set (nothing revoked); a file that
        exists but cannot be read or parsed returns ``None`` so the caller fails
        *closed* (anonymous) instead of silently un-revoking every subject.
        """
        path = getattr(self.server, "revocations_path", None)
        if not isinstance(path, Path):
            return set()
        try:
            return load_revocations(path)
        except (OSError, ValueError):  # unreadable/malformed list -> deny, never trust
            return None

    def _allow_contributions(self) -> bool:
        """Whether the contributor submission surface is enabled on this server.

        Off by default: an existing read-only deployment never grows a write path by
        surprise. A steward opts in explicitly (``serve --allow-contributions``), so
        the closed default is the safe one (least privilege, least surprise).
        """
        return bool(getattr(self.server, "allow_contributions", False))

    def _nav(self) -> str:
        """Site navigation for the current request, including Contribute when enabled."""
        return _nav_html(
            self._lang(),
            contribute=self._allow_contributions(),
            current_path=self.path,
        )

    # --- response helpers ---------------------------------------------------

    def _send(
        self, status: int, body: bytes, content_type: str, *, lang: str | None = None
    ) -> None:
        """Write a complete response with an explicit length and safe headers.

        Sets a conservative ``Content-Security-Policy`` and ``X-Content-Type-
        Options: nosniff`` so a browser will not execute inline script or sniff a
        served file into an active type (security, defense in depth). No header
        carries any request-derived value, so headers cannot leak identity.

        ``lang`` marks a response whose content was negotiated from ``_lang()``
        (I18N-13 / G11): it sets ``Content-Language`` to the served language and
        ``Vary: Accept-Language`` so a cache (browser, CDN, reverse proxy) never
        serves one reader's negotiated language to another. Machine-readable feeds
        that are always the anonymous-public view regardless of viewer (OAI-PMH,
        the sitemap, robots.txt, the Atom feed) pass no ``lang`` and get neither
        header, since their content never varies with ``Accept-Language``.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        if lang is not None and lang in i18n.SUPPORTED:
            # Write the tag from the constant map, never the value that came out
            # of _lang(), so the "only ever a shipped language" guarantee is
            # provable right here at the sink. _lang() already constrains lang to
            # i18n.SUPPORTED, but that guard is invisible to static analysis
            # through the getattr round-trip below, and one refactor away from
            # being lost. Re-asserting membership + emitting the constant closes
            # the CodeQL response-splitting/cookie-injection alerts honestly.
            self.send_header("Content-Language", i18n.SUPPORTED_HEADER[lang])
            self.send_header("Vary", "Accept-Language")
        # Persist an explicit ?lang= pick so the reader's choice survives navigation.
        # The cookie holds only the UI language code — no identity, no record id
        # (no-outing rule). Lax + HttpOnly: it is sent on top-level navigations and is
        # never exposed to script. No Secure flag, so it still works for a community
        # running ledger over plain HTTP on an inexpensive box (availability).
        chosen_lang = getattr(self, "_set_lang_cookie", None)
        if chosen_lang is not None and chosen_lang in i18n.SUPPORTED:
            self.send_header(
                "Set-Cookie",
                f"lang={i18n.SUPPORTED_HEADER[chosen_lang]}; Path=/; Max-Age=31536000; SameSite=Lax; HttpOnly",
            )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status: int, page: str) -> None:
        self._send(status, page.encode("utf-8"), "text/html; charset=utf-8", lang=self._lang())

    def _send_json(self, status: int, obj: object) -> None:
        """Serialize ``obj`` as JSON. Only DisclosedRecord-derived data is passed in,
        so the JSON cannot contain an identity field (no-outing rule)."""
        body = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", lang=self._lang())

    def _lang(self) -> str:
        """Resolve the response language: explicit choice, remembered choice, then header.

        A non-native reader gets localized UI strings and content-warning glosses
        where available, falling back to English (user research P2-1). The order is:

        1. an explicit ``?lang=`` query — the language picker — which also sets a
           ``lang`` cookie so the choice persists as the reader navigates;
        2. a previously chosen language remembered in that cookie;
        3. otherwise the browser's ``Accept-Language`` header.

        Negotiation is always against the languages ledger actually ships strings for
        (``i18n.SUPPORTED``); an unknown or unsupported value falls through to the
        next step, never to a blank page. The cookie holds only a UI language code,
        never an identity or a record reference (no-outing rule). The result is cached
        for the request so the several render calls agree."""
        cached = getattr(self, "_lang_cache", None)
        if cached is not None:
            return str(cached)
        query = parse_qs(urlsplit(self.path).query)
        choice = (query.get("lang", [""])[0] or "").strip().lower()
        if choice in i18n.SUPPORTED:
            self._set_lang_cookie = choice  # persist the explicit pick
            self._lang_cache = choice
            return choice
        remembered = self._cookie_value("lang").strip().lower()
        if remembered in i18n.SUPPORTED:
            self._lang_cache = remembered
            return remembered
        negotiated = i18n.negotiate(self.headers.get("Accept-Language"))
        self._lang_cache = negotiated
        return negotiated

    def _cookie_value(self, name: str) -> str:
        """Return the value of cookie ``name`` from the request, or ``""``.

        A small, dependency-free parse of the ``Cookie`` header; only the language
        preference cookie is read here, and it carries no identity (no-outing rule).
        The actual parsing lives in :func:`ledger.parsing.cookie_value` (FIX-09),
        fuzzed independently of this handler."""
        return cookie_value(self.headers.get("Cookie", ""), name)

    # --- routing ------------------------------------------------------------

    def do_HEAD(self) -> None:
        """Handle HEAD identically to GET but without a body (handled in `_send`)."""
        self.do_GET()

    # Pre-existing complexity (one dispatcher routes every read-only path); surfaced
    # 2026-07-05 when CQ-05's complexity gate was enabled. Waived, not re-muted:
    # this function is the disclosure/no-outing choke point, so it is deliberately
    # *not* refactored under audit time pressure — a split is tracked as a careful,
    # fully-retested follow-up, not a same-day edit to the most safety-sensitive
    # function in the repo (see ledger-REMEDIATION.md P3-2).
    def do_GET(self) -> None:  # noqa: C901
        """Route a GET request to the matching read-only handler.

        Routing is a small, explicit dispatch (predictability). Unmatched paths
        get a 404; any :class:`~ledger.errors.LedgerError` is rendered as a safe
        error page or JSON error whose message names no protected content
        (no-outing rule).
        """
        self._t0 = time.monotonic()
        parts = urlsplit(self.path)
        path = parts.path
        params = parse_qs(parts.query)
        try:
            if path == "/":
                self._handle_browse(params)
            elif path == "/search":
                self._handle_search(params)
            elif path == "/healthz":
                self._handle_healthz()
            elif path == "/status":
                self._handle_status()
            elif path == "/consent-status":
                self._handle_consent_status(params)
            elif path == "/about":
                self._handle_about()
            elif path == "/overview":
                self._handle_overview()
            elif path == "/governance":
                self._handle_governance()
            elif path == "/how-it-works":
                self._handle_how_it_works()
            elif path == "/proof":
                self._handle_proof()
            elif path == "/oai":
                self._handle_oai(params)
            elif path == "/sitemap.xml":
                self._handle_sitemap()
            elif path == "/robots.txt":
                self._handle_robots()
            elif path == "/feed.atom":
                self._handle_feed()
            elif path == "/steward":
                self._handle_steward_console()
            elif path == "/steward/audit":
                self._handle_steward_audit()
            elif path == "/contribute":
                self._handle_contribute_form()
            elif path == "/withdraw":
                self._handle_withdraw_form()
            elif path == "/edit":
                self._handle_edit_form()
            elif path.startswith("/record/") and "/file/" in path:
                rid, _, name = path[len("/record/") :].partition("/file/")
                self._handle_file(rid, name)
            elif path.startswith("/record/") and path.endswith("/consent"):
                self._handle_consent_form(path[len("/record/") : -len("/consent")])
            elif path.startswith("/record/") and path.endswith("/object"):
                self._handle_object_form(path[len("/record/") : -len("/object")])
            elif path.startswith("/record/") and path.endswith("/history"):
                self._handle_record_history(path[len("/record/") : -len("/history")], params)
            elif path.startswith("/record/"):
                self._handle_record(path[len("/record/") :], params)
            elif path == "/api/records":
                self._handle_api_records()
            elif path == "/api/search":
                self._handle_api_search(params)
            elif path == "/api/search.csv":
                self._handle_api_search_csv(params)
            elif path.startswith("/api/record/"):
                self._handle_api_record(path[len("/api/record/") :])
            elif path.startswith("/static/"):
                self._handle_static(path[len("/static/") :])
            else:
                self._handle_not_found()
        except BrokenPipeError:  # pragma: no cover - client disconnected
            pass

    def do_POST(self) -> None:  # noqa: C901
        """Route a POST: the contributor consent form and steward request actions.

        These are the only writes the site accepts. Consent submission is open (a
        claim token, not an account, proves authorship); steward actions are gated
        to a steward grant. Unmatched POSTs 404. Any error renders a safe page.
        """
        self._t0 = time.monotonic()
        path = urlsplit(self.path).path
        try:
            if path == "/contribute":
                self._post_contribute()
            elif path == "/withdraw":
                self._post_withdraw()
            elif path == "/edit":
                self._post_edit()
            elif path.startswith("/record/") and path.endswith("/consent"):
                self._post_consent(path[len("/record/") : -len("/consent")])
            elif path.startswith("/record/") and path.endswith("/object"):
                self._post_object(path[len("/record/") : -len("/object")])
            elif path.startswith("/steward/requests/") and path.endswith("/resolve"):
                rid = path[len("/steward/requests/") : -len("/resolve")]
                self._post_resolve_request(rid)
            elif path.startswith("/steward/records/") and path.endswith("/warn"):
                rid = path[len("/steward/records/") : -len("/warn")]
                self._post_steward_warn(rid)
            elif path.startswith("/steward/records/") and path.endswith("/takedown"):
                rid = path[len("/steward/records/") : -len("/takedown")]
                self._post_steward_takedown(rid)
            elif path == "/steward/submissions/withhold":
                self._post_bulk_withhold()
            elif path.startswith("/steward/submissions/") and path.endswith("/review"):
                rid = path[len("/steward/submissions/") : -len("/review")]
                self._post_review_submission(rid)
            else:
                self._handle_not_found()
        except BrokenPipeError:  # pragma: no cover - client disconnected
            pass

    def _read_form(self) -> dict[str, str]:
        """Read and parse a urlencoded POST body into a flat string mapping.

        Bounded by Content-Length; a missing/oversized length yields an empty form
        rather than reading unbounded input (robustness)."""
        return {k: v[0] for k, v in self._read_form_multi().items() if v}

    def _read_form_multi(self) -> dict[str, list[str]]:
        """Read a urlencoded POST body keeping *all* values per key.

        Same bounds as :meth:`_read_form`; used where a field repeats (e.g. a set of
        checkboxes posting the same name), which the flat reader would collapse.
        Bounding the read is this method's job (I/O policy); the decoding itself is
        :func:`ledger.parsing.parse_urlencoded_multi` (FIX-09), fuzzed independently
        of this handler."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 64 * 1024:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return parse_urlencoded_multi(raw)

    def _read_contribution(self) -> tuple[dict[str, str], tuple[str, bytes] | None]:
        """Read a contribution POST body as ``(fields, upload)``.

        Dispatches on ``Content-Type`` so the contribution form works whether it
        posts urlencoded (text-only, the long-standing path) or
        ``multipart/form-data`` (when a file is attached, backlog A2). ``upload`` is
        ``(filename, bytes)`` for the single attached file, or ``None`` when no file
        was sent. Both forms are bounded before any bytes are read: urlencoded at the
        64 KiB field cap, multipart at the upload size cap plus a small slack for the
        field parts and MIME framing, so a contribution can never read unbounded input
        (availability). The bytes are still untrusted here — :func:`upload.sniff_media_type`
        decides whether they are an accepted type before anything is stored."""
        ctype = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}, None
        if length <= 0:
            return {}, None
        if ctype.startswith("multipart/form-data"):
            # Cap the whole body at the file limit plus 1 MiB of slack for the text
            # fields and multipart boundaries/headers, so an attached file can be up to
            # MAX_UPLOAD_BYTES while the body as a whole still cannot exhaust memory.
            if length > upload.MAX_UPLOAD_BYTES + 1024 * 1024:
                return {}, None
            return self._parse_multipart(length, ctype)
        if length > 64 * 1024:
            return {}, None
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_urlencoded_multi(raw).items() if v}, None

    def _parse_multipart(
        self, length: int, content_type: str
    ) -> tuple[dict[str, str], tuple[str, bytes] | None]:
        """Parse a bounded ``multipart/form-data`` body into ``(fields, upload)``.

        Reads exactly ``length`` bytes (already capped by the caller); the actual
        MIME parsing is :func:`ledger.parsing.parse_multipart` (FIX-09), a pure
        function fuzzed independently of this handler in
        ``tests/test_parsing_fuzz.py``. Each text part becomes a ``fields`` entry;
        the first part carrying a filename becomes the single ``upload``. The
        filename is kept only to suggest a stored name and is sanitised elsewhere;
        the bytes are never trusted on type until sniffed."""
        raw = self.rfile.read(length)
        return parse_multipart(raw, content_type)

    def _consent_store(self) -> consent.ConsentRequestStore:
        return consent.ConsentRequestStore(self._archive().logs_dir / "consent-requests.json")

    def _submission_queue(self) -> review.SubmissionQueue:
        return review.SubmissionQueue(self._archive().logs_dir / "submission-queue.json")

    def _subject_token_store(self) -> consent.SubjectTokenStore:
        """The per-record store of subject-token *hashes* (RM12/EXP-04).

        Only SHA-256 hashes of the tokens minted at ingest are persisted here — never
        the clear tokens and never an identity — so it can verify a presented token
        without being able to reproduce one (no-outing rule)."""
        return consent.SubjectTokenStore(self._archive().logs_dir / "subject-tokens.json")

    def _objection_due_by(self) -> str:
        """The ISO-8601 due date for a verified subject-objection, or empty (RM12).

        Prefers the numeric ``objection_response_days`` config knob; if that is unset
        (0) it falls back to a bare day count parsed out of the free-text
        ``consent_response_time`` (e.g. "7" or "7 days"). A sentence that states no
        parseable window leaves the due date empty rather than guessing."""
        cfg = self._archive().config
        days = cfg.objection_response_days
        if days <= 0:
            days = _parse_response_days(cfg.consent_response_time)
        if days <= 0:
            return ""
        due = datetime.now(UTC) + timedelta(days=days)
        return due.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _mint_subject_tokens(self, record_id: str, count: int) -> list[str]:
        """Mint ``count`` subject tokens for ``record_id`` and persist only their hashes.

        Returns the *clear* tokens for one-time display on the receipt (out-of-band
        distribution to each named subject); the store keeps SHA-256 hashes only. No
        tokens are minted when no claim secret is configured (nothing could later
        verify them) or when ``count`` is zero (least privilege)."""
        secret = self._claim_secret()
        if not secret or count <= 0:
            return []
        tokens = [consent.issue_subject_token(record_id, i, secret) for i in range(count)]
        self._subject_token_store().register(
            record_id, [consent.subject_token_hash(token) for token in tokens]
        )
        return tokens

    def _post_consent(self, raw_id: str) -> None:
        """``POST /record/{id}/consent`` — file a contributor consent request.

        The record must be listable to the viewer (else a neutral 404 that never
        confirms a sealed record exists). A claim token issued at ingest is verified
        when a server claim secret is configured (LEDGER_CLAIM_SECRET); the request
        is queued for a steward either way and no automatic action is taken. The
        contributor's message is stored for the steward but never logged or echoed
        in an error (no-outing rule)."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        form = self._read_form()
        kind = form.get("kind", "")
        if kind not in consent.VALID_KINDS:
            self._send_html(
                400,
                _page(
                    "Invalid request",
                    lang=lang,
                    main_html=_error_main_html("Invalid request", "Please choose a valid action."),
                    nav_html=self._nav(),
                ),
            )
            return
        secret = os.environ.get("LEDGER_CLAIM_SECRET", "").encode("utf-8")
        verified = bool(secret) and consent.verify_claim_token(
            record_id, form.get("claim", ""), secret
        )
        note = "" if not secret else (" (verified)" if verified else " (claim token not verified)")
        req = consent.ConsentRequest(
            record_id=record_id, kind=kind, message=form.get("message", "")
        )
        self._consent_store().add(req)
        rt = self._archive().config.consent_response_time or "A steward will review your request."
        main_html = (
            "    <h1>Request received</h1>\n"
            f"    <p>Your request to {_esc(kind)} this record has been recorded{_esc(note)}. "
            f"A steward will review it. {_esc(rt)}</p>\n"
            f"    <p>Your reference is <code>{_esc(req.request_id)}</code>. "
            f"Check its progress anytime at "
            f'<a href="/consent-status?ref={quote(req.request_id)}">/consent-status</a>.</p>\n'
            '    <p><a href="/">Back to all records</a></p>'
        )
        self._send_html(
            200, _page("Request received", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_resolve_request(self, raw_id: str) -> None:
        """``POST /steward/requests/{id}/resolve`` — a steward closes a request."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        status = self._read_form().get("status", "acknowledged")
        if status not in consent.VALID_STATUSES:
            status = "acknowledged"
        self._consent_store().resolve(_decode_id(raw_id), status)
        self.send_response(303)
        self.send_header("Location", "/steward")
        self.end_headers()

    def _post_review_submission(self, raw_id: str) -> None:
        """``POST /steward/submissions/{id}/review`` — approve or withhold a submission.

        Steward-gated. A submission lands sealed-pending; this is where a steward
        makes the deliberate act that opens it (Hard Rule 2 — nothing publishes by
        inaction). ``publish`` opens the record to the visibility the contributor
        requested (carried on the ``account`` field); ``withhold`` restricts it to
        stewards, held for revision. Either way the decision is recorded as an
        accountable, audited :func:`ledger.moderate.change_consent` event and the
        record leaves the queue. No identity or submitted content is logged."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        record_id = _decode_id(raw_id)
        action = self._read_form().get("action", "")
        if action not in {"publish", "withhold"}:
            self._handle_not_found()
            return
        self._apply_review(record_id, action, grant.subject)
        self.send_response(303)
        self.send_header("Location", "/steward")
        self.end_headers()

    def _apply_review(self, record_id: str, action: str, actor: str) -> None:
        """Publish or withhold one pending submission, recording an audited decision.

        The shared effect behind the per-record review form and the bulk-withhold
        action: ``publish`` opens the record to the visibility the contributor
        requested (carried on the ``account`` field); ``withhold`` restricts it to
        stewards, held for revision. Either way the decision is an accountable,
        audited :func:`ledger.moderate.change_consent` event and the record leaves
        the queue. A record that has since vanished only has its stale queue entry
        cleared. No identity or submitted content is logged (no-outing rule)."""
        archive = self._archive()
        try:
            record = archive.get(record_id)
        except ObjectNotFound:
            self._submission_queue().remove(record_id)
            return
        if action == "publish":
            target = next(
                (f.policy for f in record.fields if f.name == "account"),
                AccessPolicy.COMMUNITY,
            )
            reason = "approved from the steward review queue"
        else:
            target = AccessPolicy.STEWARDS
            reason = "withheld at steward review, pending revision"
        updated, event, _action = change_consent(
            record, target, actor=actor, reason=reason, now=now_iso()
        )
        archive.apply_update(updated, event)
        self._submission_queue().remove(record_id)

    def _post_bulk_withhold(self) -> None:
        """``POST /steward/submissions/withhold`` — withhold several submissions at once.

        Steward-gated. Withholding is the *conservative* bulk action: it only ever
        restricts records to stewards (held for revision), never opens them, so a
        bulk click can never over-expose a record — publishing stays a deliberate,
        per-record act behind "open and read it first". Each selected id is withheld
        through the one audited review path; unknown ids are harmless (a missing
        record just clears its queue entry)."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        selected = self._read_form_multi().get("select", [])
        for record_id in selected:
            self._apply_review(record_id, "withhold", grant.subject)
        self.send_response(303)
        self.send_header("Location", "/steward")
        self.end_headers()

    def _redirect_steward(self) -> None:
        """Send the shared post-action redirect back to the steward console."""
        self.send_response(303)
        self.send_header("Location", "/steward")
        self.end_headers()

    def _reject_moderation(self) -> None:
        """Render the neutral 400 page shown when a moderation action is refused.

        A moderation decision requires a non-empty reason (accountability). When one is
        missing the action is *not* recorded and the steward sees a plain page telling
        them to go back and supply a rationale — no record content is echoed (no-outing
        rule)."""
        lang = self._lang()
        self._send_html(
            400,
            _page(
                i18n.t(lang, "mod_invalid_heading"),
                lang=lang,
                main_html=_error_main_html(
                    i18n.t(lang, "mod_invalid_heading"), i18n.t(lang, "mod_invalid_body")
                ),
                nav_html=self._nav(),
            ),
        )

    def _post_steward_warn(self, raw_id: str) -> None:
        """``POST /steward/records/{id}/warn`` — add a content warning in-UI (gated).

        The in-console equivalent of ``ledger cw``: a steward supplies the warning text
        and a required rationale, and the decision routes through the audited
        :func:`ledger.moderate.add_content_warning` +
        :meth:`Archive.apply_update` path, so the warning is structured metadata on the
        record and the ``MODERATION`` PREMIS event lands in the audit log the
        ``/steward/audit`` page reads (accountability). Steward-gated (a non-steward
        gets a neutral 404); an empty warning or reason is refused without recording
        anything. No identity or sealed value is logged (no-outing rule)."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        record_id = _decode_id(raw_id)
        form = self._read_form()
        warning = form.get("warning", "").strip()
        reason = form.get("reason", "")
        if not warning:
            self._reject_moderation()
            return
        archive = self._archive()
        try:
            record = archive.get(record_id)
        except ObjectNotFound:
            self._handle_not_found()
            return
        try:
            updated, event, _action = add_content_warning(
                record, warning, actor=grant.subject, reason=reason, now=now_iso()
            )
        except ModerationError:
            self._reject_moderation()
            return
        archive.apply_update(updated, event)
        self._redirect_steward()

    def _post_steward_takedown(self, raw_id: str) -> None:
        """``POST /steward/records/{id}/takedown`` — take a record down in-UI (gated).

        The in-console equivalent of ``ledger takedown``: it records the accountable
        decision and then removes every stored copy through the one shared effect
        (:func:`ledger.moderate.execute_takedown`), the same primitive the CLI uses, so
        the ``TAKEDOWN`` PREMIS event lands in the takedowns log the audit page reads
        and any sealed identity is revoked (accountability, the no-outing rule). A
        required rationale is enforced *before* any copy is touched, so a missing reason
        removes nothing. Steward-gated; a non-steward gets a neutral 404."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        record_id = _decode_id(raw_id)
        reason = self._read_form().get("reason", "")
        try:
            execute_takedown(
                self._archive(), record_id, actor=grant.subject, reason=reason, now=now_iso()
            )
        except ModerationError:
            self._reject_moderation()
            return
        self._redirect_steward()

    def _handle_object_form(
        self, raw_id: str, *, error: str | None = None, status: int = 200
    ) -> None:
        """``GET /record/{id}/object`` — a *subject's* objection form (no claim token).

        A person named or described in a record they did not contribute can ask a
        steward to review it — to redact a name or take it down (user research B3:
        subjects have agency, not only the contributor). Unlike the contributor
        consent form, it needs no claim token; the record must be listable to the
        viewer or this is a neutral 404 (it never confirms a sealed record exists).
        The objection is queued for a steward, who weighs it — nothing is automatic.
        """
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
        main_html = (
            "    <h1>Object to this record</h1>\n"
            "    <p>If you are named or described in this record and did not contribute "
            "it, you can ask a steward to review it — for example, to redact your name "
            "or take it down. A steward weighs every objection; nothing happens "
            "automatically.</p>\n"
            f"{error_html}"
            f'    <form method="post" action="/record/{quote(record_id)}/object">\n'
            '      <p><label for="message">What is your concern?</label></p>\n'
            '      <p><textarea id="message" name="message" rows="5" required></textarea></p>\n'
            '      <p><label for="token">Consent token (optional)</label><br>\n'
            '      <span class="muted">If the contributor gave you a token because this '
            "record names you, enter it here so a steward can see your objection is "
            "confirmed. Leave it blank if you do not have one.</span></p>\n"
            '      <p><input type="text" id="token" name="token" autocomplete="off"></p>\n'
            '      <p><button type="submit">Send to a steward</button></p>\n'
            "    </form>"
        )
        self._send_html(
            status, _page("Object", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_object(self, raw_id: str) -> None:
        """``POST /record/{id}/object`` — file a subject's objection for steward review.

        Queues a ``kind="object"`` request (B3). The objector's message is stored for
        the steward but never logged or echoed in an error (no-outing rule); they get
        a reference token to check progress at ``/consent-status`` (B2).

        RM12/EXP-04: if the form carries a ``token`` that verifies against a stored
        subject-token hash for this record, the objection is filed as a *verified*
        ``kind="subject-objection"`` with a recorded ``due_by`` response window;
        otherwise the existing tokenless ``kind="object"`` flow is unchanged."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        form = self._read_form()
        message = form.get("message", "").strip()
        if not message:
            self._handle_object_form(
                raw_id, error="Please describe your concern so a steward can act on it.", status=400
            )
            return
        token = form.get("token", "").strip()
        if token and self._subject_token_store().verify(record_id, token):
            req = consent.ConsentRequest(
                record_id=record_id,
                kind="subject-objection",
                message=message,
                due_by=self._objection_due_by(),
            )
        else:
            req = consent.ConsentRequest(record_id=record_id, kind="object", message=message)
        self._consent_store().add(req)
        rt = self._archive().config.consent_response_time or "A steward will review your request."
        main_html = (
            "    <h1>Your objection was received</h1>\n"
            f"    <p>It has been recorded for a steward to review. {_esc(rt)}</p>\n"
            f"    <p>Your reference is <code>{_esc(req.request_id)}</code>. "
            f"Check its progress at "
            f'<a href="/consent-status?ref={quote(req.request_id)}">/consent-status</a>.</p>\n'
            '    <p><a href="/">Back to all records</a></p>'
        )
        self._send_html(
            200, _page("Objection received", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _moderation_actions_html(self, record_id: str, lang: str) -> str:
        """The in-UI warn/takedown forms + history link for one console record row.

        Rendered as *sibling* forms inside the row (never nested, which is invalid
        HTML), each posting to a steward-gated moderation route. Both carry a mandatory
        ``reason`` field (``required`` in the browser, re-checked server-side against
        :func:`ledger.moderate._require_reason`), so a decision cannot be recorded
        without a rationale (accountability). The history link opens the record's
        version comparison. No record content beyond the opaque id is interpolated
        here (no-outing rule)."""
        rid = quote(record_id)
        warn_label = _esc(i18n.t(lang, "sw_warning_label"))
        reason_label = _esc(i18n.t(lang, "sw_reason_label"))
        return (
            f'        <form method="post" action="/steward/records/{rid}/warn">\n'
            f"          <label>{warn_label} "
            '<input type="text" name="warning" required></label>\n'
            f"          <label>{reason_label} "
            '<input type="text" name="reason" required></label>\n'
            '          <button type="submit">'
            f"{_esc(i18n.t(lang, 'sw_warn_button'))}</button>\n"
            "        </form>\n"
            f'        <form method="post" action="/steward/records/{rid}/takedown">\n'
            f"          <label>{reason_label} "
            '<input type="text" name="reason" required></label>\n'
            '          <button type="submit">'
            f"{_esc(i18n.t(lang, 'sw_takedown_button'))}</button>\n"
            "        </form>\n"
            f'        <p><a href="/record/{rid}/history">'
            f"{_esc(i18n.t(lang, 'sw_history_link'))}</a></p>\n"
        )

    def _render_request_row(self, r: consent.ConsentRequest, lang: str) -> str:
        """Render one open consent/objection request as a steward-console ``<li>``.

        Shows the kind label (a verified subject-objection reads distinctly from a
        tokenless one), the record link, the filed-at meta, and — for RM12 — the
        recorded, time-bound response: a ``due_by`` window when one was set and a
        ``resolved_at`` stamp once a steward has responded. A missing due date or
        stamp simply renders nothing (backward compatible with pre-RM12 requests)."""
        due = (
            f'\n        <span class="muted">{_esc(i18n.t(lang, "sw_due_by", when=r.due_by))}</span>'
            if r.due_by
            else ""
        )
        resolved = (
            f'\n        <span class="muted">'
            f"{_esc(i18n.t(lang, 'sw_resolved_at', when=r.resolved_at))}</span>"
            if r.resolved_at
            else ""
        )
        return (
            "      <li>\n"
            f"        <strong>{_esc(i18n.t(lang, f'req_kind_{r.kind}'))}</strong> "
            f"{_esc(i18n.t(lang, 'sw_on_record'))} "
            f'<a href="/record/{quote(r.record_id)}">{_esc(r.record_id)}</a> '
            f'<span class="muted">'
            f"({_esc(i18n.t(lang, 'sw_request_meta', when=r.created_at, ref=r.request_id))})"
            "</span>"
            f"{due}{resolved}\n"
            f'        <form method="post" action="/steward/requests/{quote(r.request_id)}/resolve">\n'
            '          <input type="hidden" name="status" value="resolved">\n'
            f'          <button type="submit">{_esc(i18n.t(lang, "sw_mark_resolved"))}</button>\n'
            "        </form>\n"
            "      </li>"
        )

    def _handle_steward_console(self) -> None:
        """``GET /steward`` — a steward's accountable console (gated).

        Shows the open consent/takedown requests a steward must act on, and is
        candid that some material may be sealed above the steward's own access
        (user research P1-5/T7). Actioning consent changes and takedowns is done
        with the audited CLI (``ledger policy`` / ``takedown`` / ``cw``); this
        console lets a steward see and close incoming requests."""
        grant = self._resolve_grant()
        lang = self._lang()
        if not grant.is_steward:
            self._handle_not_found()
            return
        archive = self._archive()
        pending = self._submission_queue().pending()
        if pending:
            sub_rows = []
            for item in pending:
                edited = ""
                try:
                    record = archive.get(item.record_id)
                    title = record.title
                    # Show what "Publish (as requested)" will actually do, so a steward
                    # never opens a record wider than the contributor asked without
                    # seeing it first (safety — no accidental over-exposure).
                    visibility = contribute.current_visibility(record)
                    target = i18n.t(lang, f"sw_vis_{visibility}")
                    cw = (
                        f' <span class="badge">{_esc(i18n.t(lang, "content_warning_heading"))}</span>'
                        if record.content_warnings
                        else ""
                    )
                    # Flag a submission the contributor corrected after submitting, so a
                    # steward part-way through review knows it changed and re-reads it.
                    corrections = sum(
                        1
                        for event in archive.record_events(item.record_id)
                        if event.event_type is PremisEventType.CORRECTION
                    )
                    if corrections >= 1:
                        # Plural-correct via ngettext (count drives singular/plural).
                        label = i18n.t(lang, "badge_edited", count=corrections)
                        edited = f' <span class="badge">{_esc(label)}</span>'
                except ObjectNotFound:
                    title = "(record unavailable)"
                    target = ""
                    cw = ""
                submitted = i18n.t(lang, "sw_submitted", when=item.submitted_at)
                # The select checkbox associates with the separate bulk-withhold form
                # via the HTML ``form`` attribute, so it can live inside this <li> (and
                # its per-item form) without illegally nesting forms.
                cid = f"sel-{quote(item.record_id)}"
                sub_rows.append(
                    "      <li>\n"
                    f'        <input type="checkbox" id="{cid}" name="select" '
                    f'value="{_esc(item.record_id)}" form="bulk-withhold">\n'
                    f'        <label for="{cid}">{_esc(i18n.t(lang, "sw_select_label"))}</label>\n'
                    f"        <strong>{_esc(title)}</strong>{cw}{edited} "
                    f'<a href="/record/{quote(item.record_id)}">{_esc(item.record_id)}</a> '
                    f'<span class="muted">({_esc(submitted)})</span>\n'
                    f"        <p>{_esc(i18n.t(lang, 'sw_would_publish_as'))} "
                    f"<strong>{_esc(target)}</strong>. "
                    f"{_esc(i18n.t(lang, 'sw_open_to_read'))}</p>\n"
                    '        <form method="post" '
                    f'action="/steward/submissions/{quote(item.record_id)}/review">\n'
                    '          <button type="submit" name="action" value="publish">'
                    f"{_esc(i18n.t(lang, 'sw_publish_button'))}</button>\n"
                    '          <button type="submit" name="action" value="withhold">'
                    f"{_esc(i18n.t(lang, 'sw_withhold_button'))}</button>\n"
                    "        </form>\n"
                    f"{self._moderation_actions_html(item.record_id, lang)}"
                    "      </li>"
                )
            # The bulk form holds only the submit button; the checkboxes above join it
            # by ``form="bulk-withhold"``. Withhold-only — the safe, conservative bulk
            # action that can never over-expose a record.
            bulk_form = (
                '    <form id="bulk-withhold" method="post" '
                'action="/steward/submissions/withhold">\n'
                f'      <p><button type="submit">{_esc(i18n.t(lang, "sw_bulk_withhold"))}'
                "</button></p>\n"
                "    </form>"
            )
            submissions_html = (
                f'    <ul class="submissions">\n{chr(10).join(sub_rows)}\n    </ul>\n{bulk_form}'
            )
        else:
            submissions_html = f"    <p>{_esc(i18n.t(lang, 'sw_no_submissions'))}</p>"
        open_reqs = self._consent_store().open_requests()
        if open_reqs:
            rows = "\n".join(self._render_request_row(r, lang) for r in open_reqs)
            requests_html = f'    <ul class="requests">\n{rows}\n    </ul>'
        else:
            requests_html = f"    <p>{_esc(i18n.t(lang, 'sw_no_requests'))}</p>"
        # The CLI command names are literal (never translated); the prose around them is.
        cli_line = (
            f"      <p>{_esc(i18n.t(lang, 'sw_cli_intro'))} <code>ledger policy</code> "
            f"{_esc(i18n.t(lang, 'sw_cli_policy_note'))} <code>ledger takedown</code>, "
            f"{_esc(i18n.t(lang, 'sw_cli_cw_note'))}</p>\n"
        )
        main_html = (
            f"    <h1>{_esc(i18n.t(lang, 'sw_console_heading'))}</h1>\n"
            '    <section aria-labelledby="sub-heading">\n'
            f'      <h2 id="sub-heading">{_esc(i18n.t(lang, "sw_submissions_heading"))}</h2>\n'
            f"      <p>{_esc(i18n.t(lang, 'sw_submissions_intro'))}</p>\n"
            f"{submissions_html}\n"
            "    </section>\n"
            '    <section aria-labelledby="req-heading">\n'
            f'      <h2 id="req-heading">{_esc(i18n.t(lang, "sw_requests_heading"))}</h2>\n'
            f"{requests_html}\n"
            "    </section>\n"
            '    <section aria-labelledby="note-heading">\n'
            f'      <h2 id="note-heading">{_esc(i18n.t(lang, "sw_before_heading"))}</h2>\n'
            f"      <p>{_esc(i18n.t(lang, 'sw_before_access'))}</p>\n"
            f"{cli_line}"
            f'      <p><a href="/steward/audit">{_esc(i18n.t(lang, "sw_view_audit"))}</a> '
            f"{_esc(i18n.t(lang, 'sw_view_audit_note'))}</p>\n"
            "    </section>"
        )
        self._send_html(
            200,
            _page(
                i18n.t(lang, "sw_console_heading"),
                lang=lang,
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    def _handle_steward_audit(self) -> None:
        """``GET /steward/audit`` — a read-only, identity-free PREMIS audit log (gated).

        A steward could verify *that* the vault never opened but could not, until now,
        read the archive's own account of what happened (user research D3). This
        renders the aggregated PREMIS events — ingestion, fixity checks, replication,
        consent/policy changes, takedowns, key rotations — newest first, as an
        accessible table. Every event is identity-free by construction
        (:meth:`Archive.audit_events`), so the log carries no contributor identity or
        sealed value. Steward-gated; a non-steward gets a neutral 404."""
        grant = self._resolve_grant()
        lang = self._lang()
        if not grant.is_steward:
            self._handle_not_found()
            return
        events = self._archive().audit_events()
        if events:
            rows = "\n".join(
                "        <tr>\n"
                f"          <td>{_esc(e.event_datetime)}</td>\n"
                f"          <td>{_esc(e.event_type.value)}</td>\n"
                f"          <td>{_esc(e.outcome)}</td>\n"
                f"          <td>{_esc(e.agent)}</td>\n"
                f"          <td>{_esc(e.linked_object or '')}</td>\n"
                f"          <td>{_esc(e.detail)}</td>\n"
                "        </tr>"
                for e in events
            )
            table = (
                "    <table>\n"
                f"      <caption>{_esc(i18n.t(lang, 'audit_caption'))}</caption>\n"
                "      <thead>\n"
                "        <tr>\n"
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_when"))}</th>\n'
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_event"))}</th>\n'
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_outcome"))}</th>\n'
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_agent"))}</th>\n'
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_object"))}</th>\n'
                f'          <th scope="col">{_esc(i18n.t(lang, "audit_col_detail"))}</th>\n'
                "        </tr>\n"
                "      </thead>\n"
                f"      <tbody>\n{rows}\n      </tbody>\n"
                "    </table>"
            )
        else:
            table = f"    <p>{_esc(i18n.t(lang, 'audit_no_events'))}</p>"
        main_html = (
            f"    <h1>{_esc(i18n.t(lang, 'audit_heading'))}</h1>\n"
            f"    <p>{_esc(i18n.t(lang, 'audit_intro'))}</p>\n"
            f"{table}\n"
            f'    <p><a href="/steward">{_esc(i18n.t(lang, "audit_back"))}</a></p>'
        )
        self._send_html(
            200,
            _page(
                i18n.t(lang, "audit_heading"), lang=lang, main_html=main_html, nav_html=self._nav()
            ),
        )

    # --- HTML routes --------------------------------------------------------

    def _handle_browse(self, params: dict[str, list[str]]) -> None:
        """``GET /`` — the accessible browse page (list + table equivalents).

        Faceted browse and search compose: ``?subject=`` / ``?type=`` / ``?language=``
        filter by a Dublin Core facet and ``?q=`` searches, and any combination
        narrows to the intersection, so a reader can search *within* a topic (user
        research P1-4)."""
        self._render_results(params)

    def _handle_search(self, params: dict[str, list[str]]) -> None:
        """``GET /search?q=`` — search disclosed records, composing with any facets.

        Search runs over already-disclosed records (so it can never surface a field
        the grant may not see) and indexes subjects, descriptions, and types, not just
        titles. The same active facets apply, so search and faceted browse are one
        finding aid rather than two. A non-Latin query shows a plain hint that search
        is English-biased."""
        self._render_results(params)

    @staticmethod
    def _active_facets(params: dict[str, list[str]]) -> list[tuple[str, str]]:
        """Every active Dublin Core facet filter, one value per field, in field order.

        Composing facets (subject AND type AND language) lets a reader narrow on more
        than one axis at once. Only the first value of each field is taken, so a
        crafted repeated param cannot AND a field against itself into nothing."""
        active: list[tuple[str, str]] = []
        for field in ("subject", "type", "language"):
            values = params.get(field)
            if values and values[0]:
                active.append((field, values[0]))
        return active

    @staticmethod
    def _apply_filters(
        records: list[DisclosedRecord],
        *,
        query: str,
        active: list[tuple[str, str]],
        date_from: str,
        date_to: str,
        sort: str,
    ) -> list[DisclosedRecord]:
        """Apply the composable discovery filters to ``records``, in order.

        Search (which ranks by relevance) then each active facet then the date range
        then an explicit sort — the same pipeline behind the browse/search page and
        the JSON search API, so both surfaces narrow a result set identically. Every
        step operates on the already-disclosed set, so no filter can surface a value a
        viewer may not see (no-outing rule)."""
        if query:
            records = search.search(records, query)
        for field, value in active:
            records = search.filter_by_facet(records, field, value)
        if date_from or date_to:
            records = search.filter_by_date_range(records, start=date_from, end=date_to)
        if sort == "newest":
            records = search.sort_by_date(records, newest=True)
        elif sort == "oldest":
            records = search.sort_by_date(records, newest=False)
        return records

    def _render_results(self, params: dict[str, list[str]]) -> None:
        """Render the browse/search page applying the query and every active facet.

        Starts from the records the grant may list, searches them by ``q`` (which also
        ranks them by relevance), then narrows by each active facet — the intersection.
        Facet counts and the sidebar are computed over the matched set so they narrow
        the *current* results, not the whole collection."""
        lang = self._lang()
        grant = self._resolve_grant()
        query = (params.get("q", [""])[0]).strip()
        active = self._active_facets(params)
        date_from = (params.get("from", [""])[0]).strip()[:20]
        date_to = (params.get("to", [""])[0]).strip()[:20]
        sort = (params.get("sort", [""])[0]).strip()

        records = self._apply_filters(
            self._archive().browse(grant),
            query=query,
            active=active,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
        )

        if query:
            heading = f"Search results for “{query}”"
        elif len(active) == 1:
            heading = f"{active[0][0].capitalize()}: {active[0][1]}"
        elif active:
            heading = "Filtered records"
        else:
            heading = "Browse the archive"
        hint = (
            '<p class="hint">Search currently matches Latin-script text; results may '
            "be incomplete for other scripts.</p>"
            if query and search.looks_non_latin(query)
            else ""
        )
        main_html = hint + _browse_main_html(
            records,
            heading=heading,
            query=query,
            lang=lang,
            active_facets=active,
            sort=sort,
            date_from=date_from,
            date_to=date_to,
            page=self._page_from(params),
            current_path=self.path,
        )
        title = f"Search — {query}" if query else "Browse"
        self._send_html(200, _page(title, lang=lang, main_html=main_html, nav_html=self._nav()))

    @staticmethod
    def _page_from(params: dict[str, list[str]]) -> int:
        """The requested 1-based page from ``?page=``, defaulting to 1.

        A missing or non-numeric value is treated as page 1; an out-of-range number
        is clamped later by :func:`ledger.pagination.paginate`, so this never raises."""
        raw = (params.get("page", ["1"])[0]).strip()
        try:
            return int(raw)
        except ValueError:
            return 1

    def _handle_record(self, raw_id: str, params: dict[str, list[str]]) -> None:
        """``GET /record/{id}`` — a single record view with a CW interstitial."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        proceed = params.get("proceed", ["0"])[0] == "1"
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            # Both "not found" and "not permitted to list" render the same neutral
            # page, so the response never reveals whether a sealed record exists
            # (confidentiality — the absence of a record leaks nothing).
            self._handle_not_found()
            return
        # Other records on the same subjects the *viewer* may list, so the related
        # links never point at anything the viewer could not already see (no-outing).
        related = search.related_by_subject(record, self._archive().browse(grant))
        main_html = _record_main_html(
            record,
            proceed=proceed,
            insider=_is_insider(grant),
            lang=self._lang(),
            base_url=self._base_url(),
            archive_name=self._archive().config.archive_name,
            related=related,
        )
        self._send_html(
            200,
            _page(
                record.title,
                lang=self._lang(),
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    def _handle_record_history(self, raw_id: str, params: dict[str, list[str]]) -> None:
        """``GET /record/{id}/history`` — a record's living-document version history (gated).

        Lists every prior manifest snapshot (timestamp + the kind of event that
        superseded it) and shows a simple, field-by-field comparison of the *current*
        record against a selected earlier version — the latest prior by default, or the
        one named by ``?v=<address>``. The comparison covers only title, description,
        content warnings, and default access; it never renders a sealed value or the
        opaque identity token, and each snapshot already passed the identity-refusing
        serializer (no-outing rule).

        Steward-gated as the safest minimal disclosure choice: history is richer than a
        single disclosed view, so it is shown only to a steward; anyone else gets the
        same neutral 404 the rest of the site returns for a record they may not see."""
        grant = self._resolve_grant()
        lang = self._lang()
        if not grant.is_steward:
            self._handle_not_found()
            return
        record_id = _decode_id(raw_id)
        archive = self._archive()
        try:
            current = archive.get(record_id)
        except ObjectNotFound:
            self._handle_not_found()
            return
        versions = archive.record_versions(record_id)
        selected = (params.get("v", [""])[0]).strip()
        addresses = [entry.get("address", "") for entry in versions]
        if selected not in addresses:
            # Default to the most recent prior snapshot (the index is oldest-first).
            selected = addresses[-1] if addresses else ""
        prior: Record | None = None
        if selected:
            try:
                prior = archive.get_version(record_id, selected)
            except (ObjectNotFound, LedgerError):
                prior = None
        main_html = _history_main_html(
            record_id,
            current=current,
            prior=prior,
            versions=versions,
            selected=selected,
            lang=lang,
        )
        self._send_html(
            200,
            _page(
                i18n.t(lang, "hist_heading"),
                lang=lang,
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    # A response-time floor for neutral 404s. A record that EXISTS but is not
    # listable returns 404 only after disclose() reads its manifest; a nonexistent
    # id returns 404 after a quick miss. Holding every neutral 404 to a fixed
    # minimum makes the two indistinguishable by timing for normal-sized records,
    # so an observer cannot confirm a sealed record exists by how fast it is denied
    # (user research P2-2). Best-effort, not a cryptographic guarantee for very
    # large manifests; the threaded server keeps the wait from blocking others.
    _NOT_FOUND_FLOOR_S = 0.02

    def _floor_not_found(self) -> None:
        elapsed = time.monotonic() - getattr(self, "_t0", time.monotonic())
        remaining = self._NOT_FOUND_FLOOR_S - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _handle_not_found(self) -> None:
        """Render the shared, neutral 404 page (reveals nothing about existence)."""
        self._floor_not_found()
        main_html = _error_main_html(
            "Not found",
            "We could not find anything at that address, or it is not available to you.",
        )
        self._send_html(
            404,
            _page(
                "Not found",
                lang=self._lang(),
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    # --- JSON routes (same disclosure gate) ---------------------------------

    def _handle_api_records(self) -> None:
        """``GET /api/records`` — JSON of every listable record's disclosed shape."""
        grant = self._resolve_grant()
        records = self._archive().browse(grant)
        reasons = _is_insider(grant)
        self._send_json(200, {"records": [r.to_dict(withheld_reasons=reasons) for r in records]})

    def _handle_api_search(self, params: dict[str, list[str]]) -> None:
        """``GET /api/search`` — JSON results for the composable discovery filters.

        Accepts the same ``q`` / ``subject`` / ``type`` / ``language`` / ``from`` /
        ``to`` / ``sort`` / ``page`` parameters as the HTML browse, applies them
        through the one shared filter pipeline, paginates the same way, and returns the
        disclosed safe shape — so an integrator gets exactly what the page shows, never
        a withheld value or an identity (no-outing rule). The body reports the active
        query and the pagination so a caller can walk the pages."""
        grant = self._resolve_grant()
        reasons = _is_insider(grant)
        records = self._apply_filters(
            self._archive().browse(grant),
            query=(params.get("q", [""])[0]).strip(),
            active=self._active_facets(params),
            date_from=(params.get("from", [""])[0]).strip()[:20],
            date_to=(params.get("to", [""])[0]).strip()[:20],
            sort=(params.get("sort", [""])[0]).strip(),
        )
        window = pagination.paginate(records, self._page_from(params), pagination.DEFAULT_PER_PAGE)
        self._send_json(
            200,
            {
                "query": (params.get("q", [""])[0]).strip(),
                "total": window.total,
                "page": window.number,
                "pages": window.pages,
                "per_page": window.per_page,
                "records": [r.to_dict(withheld_reasons=reasons) for r in window.items],
            },
        )

    #: Cap an export so a single request cannot stream an unbounded body.
    _CSV_EXPORT_CAP = 5000

    def _handle_api_search_csv(self, params: dict[str, list[str]]) -> None:
        """``GET /api/search.csv`` — the filtered result set as a CSV download.

        Same composable filters as the page and the JSON API, run through the one
        shared pipeline, but rendered as CSV for spreadsheet analysis (the whole result
        set, not one page, capped so a request stays bounded). Only the disclosed safe
        shape is written — no identity, no withheld value (no-outing rule) — and each
        cell is guarded against spreadsheet formula injection (:mod:`ledger.export`)."""
        grant = self._resolve_grant()
        records = self._apply_filters(
            self._archive().browse(grant),
            query=(params.get("q", [""])[0]).strip(),
            active=self._active_facets(params),
            date_from=(params.get("from", [""])[0]).strip()[:20],
            date_to=(params.get("to", [""])[0]).strip()[:20],
            sort=(params.get("sort", [""])[0]).strip(),
        )
        csv_text = export.records_csv(records[: self._CSV_EXPORT_CAP], base_url=self._base_url())
        body = csv_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", 'attachment; filename="search-results.csv"')
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle_api_record(self, raw_id: str) -> None:
        """``GET /api/record/{id}`` — JSON of one record's disclosed shape."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._floor_not_found()  # equalize not-found vs not-authorized timing
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, record.to_dict(withheld_reasons=_is_insider(grant)))

    # --- contributor submission (opt-in write path) -------------------------

    def _handle_contribute_form(
        self,
        *,
        error: str | None = None,
        status: int = 200,
        values: dict[str, str] | None = None,
        preview_html: str | None = None,
    ) -> None:
        """``GET /contribute`` — the accessible contribution form, when enabled.

        Returns a neutral 404 when the submission surface is off, so a read-only
        deployment never advertises a write path (least privilege). The form itself
        is plain about review-before-publish and sealed contact (no-outing rule);
        ``values`` re-fills it after a preview or a validation error, and
        ``preview_html`` shows the stranger-view panel above the form on a preview."""
        if not self._allow_contributions():
            self._handle_not_found()
            return
        lang = self._lang()
        main_html = contribute.render_contribute_main(
            self._archive().config,
            lang=lang,
            error=error,
            values=values,
            preview_html=preview_html,
        )
        self._send_html(
            status, _page("Contribute", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_contribute(self) -> None:
        """``POST /contribute`` — preview, edit, or submit a contribution.

        The form's primary action is **preview**: the contributor first sees exactly
        what a stranger would see if it were published at the requested visibility,
        with nothing stored. **edit** returns to the form with their entries intact.
        Only **submit** stores it — sealed-pending, through the one ingest path, with
        any contact sealed into the vault and never echoed (the no-outing rule). A
        validation error re-renders the form (with the entries kept); a sealing
        failure declines without naming anything submitted."""
        if not self._allow_contributions():
            self._handle_not_found()
            return
        form, attachment = self._read_contribution()
        action = form.get("action", "preview")
        try:
            submission = contribute.parse_submission(form, self._archive().config)
        except ValidationError as exc:
            message = i18n.t(self._lang(), exc.code, **exc.fields)
            self._handle_contribute_form(error=message, status=400, values=form)
            return
        if action != "submit":
            self._render_contribute_preview(submission, form)
            return
        # Validate any attached file from its *bytes* before storing anything: an
        # oversized or non-allowlisted upload is refused with a re-rendered form, and
        # the contributor's text is kept. The stored media type is server-determined
        # by sniffing, never the client's filename or Content-Type (backlog A2).
        payload: dict[str, Path]
        with tempfile.TemporaryDirectory(prefix="ledger-upload-") as tmpdir:
            if attachment is not None:
                error = self._stage_upload(attachment, submission.record, Path(tmpdir))
                if error is not None:
                    self._handle_contribute_form(error=error, status=400, values=form)
                    return
                stored_name = submission.record.payloads[0].filename
                payload = {stored_name: Path(tmpdir) / stored_name}
            else:
                payload = {}
            stamp = now_iso()
            try:
                self._archive().ingest(
                    payload,
                    submission.record,
                    identity=submission.identity,
                    agent="contribution",
                    now=stamp,
                )
            except LedgerError:
                # Name nothing the contributor submitted; just decline cleanly.
                self._handle_contribute_form(
                    error=i18n.t(self._lang(), "err_save_failed"),
                    status=503,
                    values=form,
                )
                return
        # Queue it for steward review. The entry is identity-free (id + timestamp);
        # the record stays sealed-pending until a steward acts (Hard Rule 2).
        record_id = submission.record.record_id
        self._submission_queue().add(record_id, now=stamp)
        # Hand the contributor a reference + a claim token (a capability, not an
        # identity) so they can withdraw the submission themselves while it is still
        # pending. Only when a claim secret is configured; otherwise the thanks page
        # stays generic and self-withdrawal is unavailable.
        claim_token = self._claim_token(record_id)
        # RM12/EXP-04: mint one subject token per named subject the contributor
        # declared. The clear tokens are shown once on this receipt for out-of-band
        # hand-off; only their SHA-256 hashes are persisted (no identities, no clear
        # tokens on disk — mirror the contributor claim token above).
        subject_tokens = self._mint_subject_tokens(
            record_id, _named_subjects_count(form.get("named_subjects_count", ""))
        )
        lang = self._lang()
        self._send_html(
            200,
            _page(
                "Thank you",
                lang=lang,
                main_html=contribute.render_thanks_main(
                    reference=record_id if claim_token else None,
                    claim_token=claim_token,
                    subject_tokens=subject_tokens,
                    lang=lang,
                ),
                nav_html=self._nav(),
            ),
        )

    def _stage_upload(
        self, attachment: tuple[str, bytes], record: Record, tmpdir: Path
    ) -> str | None:
        """Validate and stage an attached file, or return a safe error to show.

        The bytes are the only thing trusted: the file is refused if it is larger than
        :data:`upload.MAX_UPLOAD_BYTES` or if :func:`upload.sniff_media_type` does not
        recognise it as one of the allowlisted types. On success the bytes are written
        under ``tmpdir`` with a sanitised filename and a :class:`PayloadFile` is
        pre-declared on ``record`` so the one ingest path stores it with the
        *server-sniffed* media type and the record's sealed-pending policy — never a
        type taken from the client. The returned error names no submitted value
        (no-outing rule). On success returns ``None``."""
        filename, data = attachment
        lang = self._lang()
        if len(data) > upload.MAX_UPLOAD_BYTES:
            megabytes = upload.MAX_UPLOAD_BYTES // (1024 * 1024)
            return i18n.t(lang, "err_file_too_large", max=megabytes)
        media_type = upload.sniff_media_type(data)
        if media_type is None:
            return i18n.t(lang, "err_file_type", types=", ".join(upload.ALLOWED_TYPES))
        # Path(...).name reduces the sanitized name to a single component, so the
        # write target is provably a direct child of tmpdir (no traversal even if
        # _safe_filename ever regresses). _safe_filename already strips separators;
        # this makes that invariant explicit at the write sink.
        safe = Path(_safe_filename(filename) or "upload").name or "upload"
        (tmpdir / safe).write_bytes(data)
        # The payload follows the record's sealed-pending default, so it is invisible
        # until a steward reviews it — exactly like the rest of the submission. The
        # address is a placeholder; the one ingest path recomputes it from the bytes.
        record.payloads = [
            PayloadFile(
                filename=safe,
                address=ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64),
                media_type=media_type,
                policy=record.default_policy,
            )
        ]
        return None

    def _render_contribute_preview(
        self, submission: contribute.Submission, form: dict[str, str]
    ) -> None:
        """Re-render the form with a "what a stranger sees" panel, storing nothing.

        Simulates the published state (default policy opened to the requested
        visibility) and discloses it to the anonymous public through the single
        disclosure chokepoint, so the preview cannot show a stranger more than a real
        read path would. When the record would not be listable to a stranger, the
        panel honestly says a stranger sees nothing. The contributor's entries are
        re-filled into the form below the panel — their sealed contact is never in
        the panel itself."""
        preview = contribute.preview_record(submission)
        now = now_iso()
        stranger_view = (
            disclose(preview, anonymous(), now) if is_listable(preview, anonymous(), now) else None
        )
        visibility = form.get("visibility") or "community"
        panel = contribute.render_preview_panel(
            stranger_view, visibility=visibility, lang=self._lang()
        )
        self._handle_contribute_form(values=form, preview_html=panel)

    # --- contributor self-service withdrawal --------------------------------

    def _claim_secret(self) -> bytes:
        """The configured claim secret as bytes, or empty when none is set."""
        return os.environ.get("LEDGER_CLAIM_SECRET", "").encode("utf-8")

    def _claim_token(self, record_id: str) -> str | None:
        """A claim token for ``record_id``, or ``None`` when no claim secret is set."""
        secret = self._claim_secret()
        return consent.issue_claim_token(record_id, secret) if secret else None

    def _withdrawal_enabled(self) -> bool:
        """Self-withdrawal needs both the contribution surface and a claim secret.

        Without contributions there is nothing to withdraw; without a claim secret no
        token was ever issued, so authorship cannot be proven and the surface stays
        closed (least privilege)."""
        return self._allow_contributions() and bool(self._claim_secret())

    def _handle_withdraw_form(self, *, error: str | None = None, reference: str = "") -> None:
        """``GET /withdraw`` — the form to withdraw a still-pending submission.

        A neutral 404 when self-withdrawal is off, so a deployment without it never
        advertises the path (least privilege)."""
        if not self._withdrawal_enabled():
            self._handle_not_found()
            return
        lang = self._lang()
        main_html = contribute.render_withdraw_main(error=error, reference=reference, lang=lang)
        self._send_html(
            200, _page("Withdraw", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_withdraw(self) -> None:
        """``POST /withdraw`` — withdraw a pending submission given a valid claim token.

        Permitted only while the submission is *still pending* (in the review queue,
        never published): a contributor may freely undo their own not-yet-public
        submission, but once a steward has published a record it is governed by the
        normal consent/takedown path, not a self-service form. A valid claim token
        proves authorship.

        Every failure — unknown reference, bad token, or a record that is no longer
        pending — returns the *same* neutral error, so the endpoint cannot be used as
        an oracle to test whether a record exists or what state it is in (no-outing
        rule). On success the one shared removal effect erases every copy and revokes
        any sealed identity, the decision is recorded in the takedowns log, and the
        confirmation names nothing that was withdrawn."""
        if not self._withdrawal_enabled():
            self._handle_not_found()
            return
        form = self._read_form()
        reference = (form.get("ref") or "").strip()
        claim = (form.get("claim") or "").strip()
        secret = self._claim_secret()
        queue = self._submission_queue()
        authorized = (
            bool(reference)
            and consent.verify_claim_token(reference, claim, secret)
            and queue.contains(reference)
        )
        if not authorized:
            # One neutral message for every failure: never confirm a record exists.
            self._handle_withdraw_form(
                error=i18n.t(self._lang(), "err_withdraw_failed"),
                reference=reference,
            )
            return
        now = now_iso()
        archive = self._archive()
        # Record the accountable decision first (its "why" must outlive the data),
        # then erase every copy through the one shared removal effect. The actor is
        # the contributor themselves; the reason names no one.
        event, _action = takedown(
            reference,
            actor="contributor",
            reason="contributor withdrawal before publication",
            now=now,
        )
        archive.log_takedown(event)
        archive.remove_all_copies(reference)
        queue.remove(reference)
        lang = self._lang()
        self._send_html(
            200,
            _page(
                "Withdrawn",
                lang=lang,
                main_html=contribute.render_withdraw_done_main(lang=lang),
                nav_html=self._nav(),
            ),
        )

    def _handle_edit_form(
        self, *, error: str | None = None, values: dict[str, str] | None = None
    ) -> None:
        """``GET /edit`` — the form to load and correct a still-pending submission.

        Gated identically to withdrawal (contributions on and a claim secret set);
        a neutral 404 otherwise so a deployment without it never advertises the path."""
        if not self._withdrawal_enabled():
            self._handle_not_found()
            return
        lang = self._lang()
        main_html = contribute.render_edit_main(
            self._archive().config, lang=lang, values=values, error=error
        )
        self._send_html(200, _page("Edit", lang=lang, main_html=main_html, nav_html=self._nav()))

    def _post_edit(self) -> None:
        """``POST /edit`` — load or save an edit to a pending submission.

        Authorship is proven by the claim token on every POST, and editing is allowed
        only while the submission is *still pending* (in the review queue) — once a
        steward has published a record it follows the normal governance path, not this
        self-service form. ``action=load`` pulls the current values into the form so
        the contributor can see what they are changing; ``action=save`` validates and
        persists the correction through the one update path, recording a PREMIS
        CORRECTION event. A bad reference/code returns the same neutral error as
        withdrawal, so the endpoint is no existence oracle (no-outing rule). The sealed
        contact is never loaded back or editable here."""
        if not self._withdrawal_enabled():
            self._handle_not_found()
            return
        form = self._read_form()
        reference = (form.get("ref") or "").strip()
        claim = (form.get("claim") or "").strip()
        secret = self._claim_secret()
        queue = self._submission_queue()
        authorized = (
            bool(reference)
            and consent.verify_claim_token(reference, claim, secret)
            and queue.contains(reference)
        )
        archive = self._archive()
        record = None
        if authorized:
            try:
                record = archive.get(reference)
            except LedgerError:
                record = None
        if record is None:
            self._handle_edit_form(error=i18n.t(self._lang(), "err_edit_failed"), values=form)
            return

        if form.get("action") == "save":
            try:
                updated = contribute.apply_edit(record, form, archive.config)
            except ValidationError as exc:
                message = i18n.t(self._lang(), exc.code, **exc.fields)
                self._handle_edit_form(error=message, values=form)
                return
            event = PremisEvent(
                event_type=PremisEventType.CORRECTION,
                agent="contributor",
                outcome="success",
                detail="contributor edited a pending submission",
                linked_object=reference,
                event_datetime=now_iso(),
            )
            archive.apply_update(updated, event)
            lang = self._lang()
            self._send_html(
                200,
                _page(
                    "Edited",
                    lang=lang,
                    main_html=contribute.render_edit_done_main(lang=lang),
                    nav_html=self._nav(),
                ),
            )
            return

        # action=load (default): prefill the form from the current record.
        account = record.field_named("account")
        dc = record.dublin_core
        values = {
            "ref": reference,
            "claim": claim,
            "title": record.title,
            "summary": dc.description[0] if dc.description else "",
            "subject": ", ".join(dc.subject),
            "type": dc.type[0] if dc.type else "",
            "date": dc.date[0] if dc.date else "",
            "language": dc.language[0] if dc.language else "",
            "account": account.value if account is not None else "",
            "visibility": contribute.current_visibility(record),
        }
        for warning in record.content_warnings:
            values[f"cw_{warning}"] = "1"
        self._handle_edit_form(values=values)

    # --- health -------------------------------------------------------------

    def _handle_healthz(self) -> None:
        """``GET /healthz`` — JSON health, with counts gated to stewards.

        An outsider gets only ``status`` and an ``all_verified`` boolean. The
        absolute counts (bags audited / files checked) include sealed and
        community records, so revealing them to the public would leak the TOTAL
        size of the archive — letting an observer learn that sealed records exist
        and poll for when one is added (user research P2-2). Only a steward grant
        sees the numbers; a monitor uses a provisioned grant. No path, digest, id,
        or identity ever appears (no-outing rule).
        """
        archive = self._archive()
        grant = self._resolve_grant()
        # Structural readiness first: a liveness probe must fail when the store or
        # vault is unreachable, not just when a checksum drifts. The reason code is
        # generic infrastructure state — never a path, id, or identity (no-outing).
        ready, reason = archive.check_readiness()
        if not ready:
            self._send_json(
                503, {"status": "degraded", "all_verified": False, "ready": False, "reason": reason}
            )
            return
        try:
            reports = archive.audit_fixity()
        except LedgerError:
            self._send_json(503, {"status": "degraded", "all_verified": False, "ready": True})
            return
        passed = sum(1 for _name, r in reports if r.ok)
        failed = len(reports) - passed
        status = "ok" if failed == 0 else "degraded"
        code = 200 if failed == 0 else 503
        body: dict[str, object] = {
            "status": status,
            "all_verified": failed == 0,
            "ready": True,
            # A single opaque commitment over every PREMIS chain head (FIX-06):
            # safe for anyone, since — unlike the per-bag counts below — it
            # reveals neither how many bags exist nor which ones they are
            # (no-outing / P2-2), but changes the instant any recorded history is
            # rewritten. A community member can note it over time and cross-check.
            "chain_head": archive.chain_head_summary(),
        }
        if grant.is_steward:
            body["fixity"] = {
                "bags_audited": len(reports),
                "bags_passed": passed,
                "bags_failed": failed,
                "files_checked": sum(r.checked for _name, r in reports),
            }
        self._send_json(code, body)

    def _handle_status(self) -> None:
        """``GET /status`` — a human-readable health page (not raw JSON).

        The old "Status" nav target served raw JSON, which alarmed non-technical
        users ("have I broken it?") and was an unreadable wall of punctuation to a
        screen reader (user research P1-1). This renders the same fixity summary as
        a plain, accessible page; the JSON stays at ``/healthz`` for monitors.
        """
        lang = self._lang()
        archive = self._archive()
        grant = self._resolve_grant()
        try:
            reports = archive.audit_fixity()
            passed = sum(1 for _n, r in reports if r.ok)
            total = len(reports)
            files = sum(r.checked for _n, r in reports)
            healthy = passed == total
            headline = (
                "Everything is healthy." if healthy else "Some records need a steward's attention."
            )
            # Absolute counts include sealed records, so the exact numbers are shown
            # only to a steward; everyone else gets the qualitative headline (P2-2).
            if grant.is_steward and total:
                detail = (
                    f"{passed} of {total} record package(s) passed every integrity check "
                    f"({files} file checksum(s) verified)."
                )
            elif healthy:
                detail = "Every stored record passed its most recent integrity check."
            else:
                detail = "One or more records did not pass their integrity check."
        except LedgerError:
            headline, detail = "Status check failed.", "An integrity check could not be completed."
        main_html = (
            f"    <h1>Archive status</h1>\n"
            f"    <p><strong>{_esc(headline)}</strong></p>\n"
            f"    <p>{_esc(detail)}</p>\n"
            '    <p class="muted">Machine-readable health is at '
            '<a href="/healthz">/healthz</a>.</p>'
        )
        self._send_html(200, _page("Status", lang=lang, main_html=main_html, nav_html=self._nav()))

    def _handle_consent_status(self, params: dict[str, list[str]]) -> None:
        """``GET /consent-status`` — let a contributor check a request's progress.

        A contributor who filed a withdraw/tighten/correct/contact request was given
        a random reference token; entering it here shows whether a steward has acted
        (user research T4/B2 — "revocable was true in the room, not on the website").
        The token is the only key, so no one without it learns anything. The page
        shows the *kind*, when it was filed, and a plain-language status — never the
        contributor's private message, and nothing identity-bearing (no-outing)."""
        lang = self._lang()
        ref = (params.get("ref", [""])[0] or "").strip()
        if not ref:
            result_html = ""
        else:
            req = self._consent_store().get(ref)
            if req is None:
                result_html = (
                    f'    <p class="error" role="status">{_esc(i18n.t(lang, "cs_not_found"))}</p>\n'
                )
            else:
                kind = i18n.t(lang, f"req_kind_{req.kind}")
                status = i18n.t(lang, f"cs_status_{req.status}")
                result_html = (
                    '    <section class="status" role="status" '
                    f'aria-label="{_esc(i18n.t(lang, "cs_status_aria"))}">\n'
                    f"      <p>{_esc(i18n.t(lang, 'cs_request_label', kind=kind))}</p>\n"
                    f"      <p>{_esc(i18n.t(lang, 'cs_filed_label', when=req.created_at))}</p>\n"
                    f"      <p><strong>{_esc(i18n.t(lang, 'cs_status_label', status=status))}"
                    "</strong></p>\n"
                    f"{self._takedown_progress_html(lang, req.record_id)}"
                    "    </section>\n"
                )
        main_html = (
            f"    <h1>{_esc(i18n.t(lang, 'cs_heading'))}</h1>\n"
            f"    <p>{_esc(i18n.t(lang, 'cs_intro'))}</p>\n"
            f"{result_html}"
            '    <form method="get" action="/consent-status">\n'
            "      <p>\n"
            f'        <label for="ref">{_esc(i18n.t(lang, "cs_ref_label"))}</label>\n'
            f'        <input type="text" id="ref" name="ref" value="{_esc(ref)}">\n'
            "      </p>\n"
            f'      <p><button type="submit">{_esc(i18n.t(lang, "cs_button"))}</button></p>\n'
            "    </form>\n"
        )
        self._send_html(
            200,
            _page(i18n.t(lang, "cs_heading"), lang=lang, main_html=main_html, nav_html=self._nav()),
        )

    def _takedown_progress_html(self, lang: str, record_id: str) -> str:
        """Per-location takedown completion for ``record_id``, or "" if none.

        For a record that has actually been taken down there is a durable tombstone
        recording which storage locations have confirmed the removal. This renders
        that honestly — "2 of 3 locations have confirmed; mirror-b pending" — so a
        contributor is never told a removal is complete while an offline replica
        still holds a copy (user research T4/B2, "revocable was true in the room").
        It shows only counts and location *names*, never any record content
        (no-outing rule). A record with no tombstone yields the empty string, so a
        non-takedown request renders exactly as before.
        """
        archive = self._archive()
        confirmed = TombstoneStore(archive.logs_dir).status(record_id)
        if confirmed is None:
            return ""
        # The full set of copy locations: the authoritative primary store plus every
        # configured mirror, in a stable order, unioned with any location that has a
        # receipt but is no longer configured (so the count never understates).
        known = [PRIMARY_LOCATION, *(loc.name for loc in archive.config.locations)]
        seen: set[str] = set()
        expected: list[str] = []
        for name in [*known, *confirmed]:
            if name not in seen:
                seen.add(name)
                expected.append(name)
        pending = [name for name in expected if name not in confirmed]
        lines = [
            "      <p>"
            + _esc(
                i18n.t(
                    lang,
                    "cs_takedown_progress",
                    confirmed=len(expected) - len(pending),
                    total=len(expected),
                )
            )
            + "</p>\n"
        ]
        if pending:
            lines.append(
                "      <p>"
                + _esc(i18n.t(lang, "cs_takedown_pending", locations=", ".join(pending)))
                + "</p>\n"
            )
        else:
            lines.append("      <p>" + _esc(i18n.t(lang, "cs_takedown_complete")) + "</p>\n")
        return "".join(lines)

    # --- plain-language safety surface (user research P0-4) -----------------

    def _info_page(self, title: str, heading: str, paragraphs: list[str]) -> None:
        lang = self._lang()
        body = "\n".join(f"    <p>{_esc(p)}</p>" for p in paragraphs if p)
        main_html = f"    <h1>{_esc(heading)}</h1>\n{body}"
        self._send_html(200, _page(title, lang=lang, main_html=main_html, nav_html=self._nav()))

    def _handle_about(self) -> None:
        """``GET /about`` — who runs the archive and how it protects people."""
        cfg = self._archive().config
        self._info_page(
            "About",
            f"About {cfg.archive_name}",
            [
                cfg.about,
                "Who runs this archive: " + cfg.operators if cfg.operators else "",
                "How to reach us: " + cfg.contact if cfg.contact else "",
            ],
        )

    def _handle_governance(self) -> None:
        """``GET /governance`` — how stewards are chosen and held accountable."""
        cfg = self._archive().config
        self._info_page(
            "Governance",
            "Governance",
            [
                cfg.steward_vetting,
                "Stewards can read access-restricted content in order to do their work, "
                "but they can never see a contributor's sealed identity, and content "
                "sealed with the 'sealed' policy is restricted from everyone — including "
                "stewards. Every steward action records who acted and why.",
                "Consent and takedown requests: " + cfg.consent_response_time
                if cfg.consent_response_time
                else "",
            ],
        )

    def _handle_how_it_works(self) -> None:
        """``GET /how-it-works`` — plain-language explanation + how to contribute."""
        self._info_page(
            "How it works",
            "How this protects you, and how to contribute",
            [
                "You can publish a story while sealing the names, the location, or your "
                "own identity. Sealed parts are shown to you as 'withheld', never exposed.",
                "Your identity as a contributor is stored separately and encrypted, and is "
                "shown on no page here — not even to a steward — unless you explicitly grant it.",
                "You stay in control: from any record you can ask a steward to tighten access "
                "or take it down (see the 'Manage or withdraw consent' link on each record).",
                "Contributing currently happens with a steward's help so your choices about "
                "what to seal are made deliberately. See the proof that we keep these promises "
                "at /proof.",
            ],
        )

    def _handle_proof(self) -> None:
        """``GET /proof`` — explain the verifiable no-outing guarantee (show, don't tell)."""
        chain_head = self._archive().chain_head_summary()
        self._info_page(
            "Our promise, proven",
            "We prove the promise, we don't just state it",
            [
                "The claim 'contributor identities are never shown here' is not an honour-system "
                "promise — it is a test the software must pass on every build.",
                "A contributor's identity is stored only as an opaque token plus encrypted data "
                "in a separate vault. The record a page is built from has no place to put an "
                "identity, so there is nothing to leak.",
                "The project's audit ingests a sentinel identity and then checks that it appears "
                "on no page, in no data file, in no backup, and in no log — and that a sealed "
                "record cannot even be confirmed to exist by an outsider.",
                "The same discipline applies to the record of what happened here: every "
                "preservation and moderation event is hash-chained, so editing history after "
                "the fact — even by someone with direct access to the disk — changes this "
                "archive's chain head. Anyone who has previously noted the value below can "
                "confirm it has only ever moved forward: chain head "
                f"{chain_head} (also published, for stewards, at /healthz).",
            ],
        )

    # --- content retrieval (user research P0-4 / C4) -----------------------

    def _handle_file(self, raw_id: str, raw_name: str) -> None:
        """``GET /record/{id}/file/{name}`` — download a permitted payload file.

        Access is enforced by disclosure: only a payload present in the viewer's
        DisclosedRecord (i.e. one their grant may see) can be fetched; anything else
        is an indistinguishable 404. Bytes are read from the content-addressed store
        by their address, so what is served is fixity-verified by construction
        (integrity). Records were previously unreadable — the filename was an inert
        false affordance (user research C4).

        Streams the file in :data:`~ledger.fixity.CHUNK_SIZE` windows rather than
        reading it whole into memory first (FIX-03): a multi-gigabyte oral-history
        video must not cost gigabytes of RSS to serve on the "one inexpensive box"
        the archive targets. A single ``Range: bytes=...`` request is honored with
        a ``206 Partial Content`` response (RFC 9110 §14) so a browser can seek
        within served audio/video instead of re-downloading the whole file; a
        malformed unit, a multi-range request, or no header at all falls back to a
        full ``200`` response, and an unsatisfiable range gets a clean ``416``.
        """
        record_id = _decode_id(raw_id)
        filename = _decode_id(raw_name)
        grant = self._resolve_grant()
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        payload = next((p for p in record.payloads if p.filename == filename), None)
        if payload is None:
            self._handle_not_found()
            return
        # get_path/stat/open are all inside the not-found mapping: if the object
        # vanishes between any two of them (TOCTOU), the answer stays the same
        # indistinguishable 404 as any other unfetchable record (anti-enumeration).
        handle = None
        try:
            path = self._archive().store.get_path(payload.address)
            size = path.stat().st_size
            if self.command != "HEAD":
                handle = path.open("rb")
        except (ObjectNotFound, LedgerError, OSError):
            if handle is not None:  # pragma: no cover - defensive
                handle.close()
            self._handle_not_found()
            return
        try:
            self._send_file_response(handle, payload, filename, size)
        finally:
            if handle is not None:
                handle.close()

    def _send_file_response(
        self, handle: BinaryIO | None, payload: PayloadFile, filename: str, size: int
    ) -> None:
        """Write the (range-aware) file response headers, then stream the bytes.

        ``handle`` is the already-open payload handle (``None`` for HEAD). A
        syntactically valid but unsatisfiable ``Range`` gets a clean ``416``;
        otherwise a single satisfiable range gets ``206`` and anything else the
        full ``200`` (RFC 9110 §14). The caller owns closing ``handle``.
        """
        try:
            byte_range = _parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return
        if byte_range is None:
            status, start, end = 200, 0, size - 1
        else:
            status, (start, end) = 206, byte_range
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", payload.media_type or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("X-Content-Type-Options", "nosniff")
        # RFC 6266: a percent-encoded filename* is a recognized-safe way to carry
        # the (already CR/LF/quote-stripped) name, and it renders non-ASCII names
        # correctly for a bilingual archive. Keep an ASCII filename= fallback.
        safe_name = _safe_filename(filename)
        self.send_header(
            "Content-Disposition",
            f"inline; filename=\"{safe_name}\"; filename*=UTF-8''{quote(safe_name, safe='')}",
        )
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if handle is not None:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # --- consent (user research P0-2): the contributor's front door ---------

    def _handle_consent_form(self, raw_id: str) -> None:
        """``GET /record/{id}/consent`` — the contributor's consent/withdrawal form.

        Lets the *contributor* (not only a steward) act on the promise that consent
        is revocable. Submitting requires a claim token issued at ingest, proving
        authorship without an account. The record must at least be listable to the
        viewer or this is a neutral 404 (it never confirms a sealed record exists).
        """
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        cfg = self._archive().config
        rt = cfg.consent_response_time or "We will respond as soon as we can."
        main_html = (
            "    <h1>Manage or withdraw your consent</h1>\n"
            "    <p>If you contributed this record, you can ask a steward to tighten its "
            "access, correct it, or take it down. You will need the claim token you were "
            "given when you contributed.</p>\n"
            f"    <p>{_esc(rt)}</p>\n"
            f'    <form method="post" action="/record/{quote(record_id)}/consent">\n'
            '      <p><label for="kind">What would you like to do?</label><br>\n'
            '      <select id="kind" name="kind">\n'
            '        <option value="withdraw">Take this record down</option>\n'
            '        <option value="tighten">Tighten who can see it</option>\n'
            '        <option value="correct">Correct something</option>\n'
            '        <option value="contact">Contact a steward</option>\n'
            "      </select></p>\n"
            '      <p><label for="claim">Your claim token</label><br>\n'
            '      <input id="claim" name="claim" type="text" autocomplete="off" required></p>\n'
            '      <p><label for="message">Message (optional)</label><br>\n'
            '      <textarea id="message" name="message" rows="4"></textarea></p>\n'
            '      <p><button type="submit">Send request</button></p>\n'
            "    </form>"
        )
        self._send_html(
            200, _page("Manage consent", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    # --- interoperability (user research P2-3): OAI-PMH + sitemap ----------

    def _public_records(self) -> list[DisclosedRecord]:
        """Records disclosed to the anonymous public — the only set harvest exposes."""
        return self._archive().browse(anonymous())

    def _base_url(self) -> str:
        host = self.headers.get("Host", "localhost")
        return f"http://{host}"

    def _handle_overview(self) -> None:
        """``GET /overview`` — an at-a-glance summary of the public collection.

        Summarises only the anonymous-public set, so the totals, top facets, and date
        span describe what is publicly visible and never reveal the existence or count
        of sealed records (no-outing rule / P2-2). Each facet links into the faceted
        browse, turning the overview into a finding aid (P2-3)."""
        lang = self._lang()
        main_html = _overview_main_html(self._public_records(), lang=lang)
        self._send_html(
            200,
            _page(
                i18n.t(lang, "overview_heading"),
                lang=lang,
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    def _handle_oai(self, params: dict[str, list[str]]) -> None:
        """``GET /oai`` — a minimal OAI-PMH provider over public records only."""
        cfg = self._archive().config
        flat = {k: v[0] for k, v in params.items() if v}
        status, xml = oai.oai_response(
            flat.get("verb", ""),
            flat,
            records=self._public_records(),
            archive_name=cfg.archive_name,
            base_url=self._base_url() + "/oai",
            admin_email=cfg.contact or "",
            now=now_iso(),
        )
        self._send(status, xml.encode("utf-8"), "text/xml; charset=utf-8")

    def _handle_sitemap(self) -> None:
        """``GET /sitemap.xml`` — public record URLs for crawlers (discoverability)."""
        ids = [r.record_id for r in self._public_records()]
        xml = oai.sitemap_xml(ids, self._base_url())
        self._send(200, xml.encode("utf-8"), "application/xml; charset=utf-8")

    def _handle_robots(self) -> None:
        """``GET /robots.txt`` — guide crawlers to public content, away from the rest.

        Points crawlers at the sitemap so the *public* records are discoverable (the
        harvestability user research P2-3 asks for), while disallowing the write and
        operator surfaces — the contribution, withdrawal, edit, and steward paths, the
        JSON API, and the consent-status lookup — so a search engine never indexes a
        form or a steward console. It is advisory, not access control (those paths are
        already gated or carry no listable content); it keeps non-content pages out of
        public indexes (privacy hygiene). No request value enters the response."""
        root = self._base_url()
        lines = [
            "User-agent: *",
            "Disallow: /steward",
            "Disallow: /contribute",
            "Disallow: /withdraw",
            "Disallow: /edit",
            "Disallow: /api/",
            "Disallow: /consent-status",
            f"Sitemap: {root}/sitemap.xml",
            "",
        ]
        self._send(200, "\n".join(lines).encode("utf-8"), "text/plain; charset=utf-8")

    def _handle_feed(self) -> None:
        """``GET /feed.atom`` — an Atom feed of the most recent public records.

        Always the *anonymous public* view, regardless of the viewer, so this
        cacheable, aggregator-fetched surface can never carry community-only or
        sealed content (least privilege). It re-serializes only already-disclosed
        public records, so no identity or sealed value can appear (no-outing rule).
        """
        cfg = self._archive().config
        xml = oai.atom_feed_xml(
            self._public_records(),
            archive_name=cfg.archive_name,
            base_url=self._base_url(),
            now=now_iso(),
        )
        self._send(200, xml.encode("utf-8"), "application/atom+xml; charset=utf-8")

    # --- static files (path-traversal safe) --------------------------------

    def _handle_static(self, rel: str) -> None:
        """Serve a file from the ``web/static`` allowlist, or 404.

        The decoded request value is matched by *name* against ``_STATIC_FILES``,
        a map built once at import from the real files under the canonical static
        root. Request input is only ever a dictionary key here, never part of a
        path expression, so a ``../``, an absolute path, or a symlink name simply
        misses the map and 404s — traversal is not expressible (securability). An
        unknown suffix falls back to ``application/octet-stream`` and is never
        treated as active content.
        """
        candidate = _STATIC_FILES.get(_decode_id(rel))
        if candidate is None:
            self._handle_not_found()
            return
        content_type = _STATIC_CONTENT_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        raw = candidate.read_bytes()
        # Cache + conditional GET so a metered, intermittent mobile connection does
        # not re-download the stylesheet every visit (user research P3-1). The ETag
        # is a content hash, so it changes only when the file does.
        etag = '"' + hashlib.sha256(raw).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        body = gzip.compress(raw) if accepts_gzip else raw
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("ETag", etag)
        if accepts_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


# --- module-level render helpers (shared by routes) -------------------------
class _UnsatisfiableRange(ValueError):
    """A syntactically valid ``Range`` header names bytes ``size`` doesn't have."""


def _parse_suffix_range(end_s: str, size: int) -> tuple[int, int] | None:
    """Parse the suffix form ``bytes=-N`` (the last N bytes of the resource).

    A suffix range against an empty resource is unsatisfiable (RFC 9110 §14.1.2:
    there is no last byte to name), so ``size == 0`` raises rather than yielding
    a malformed ``206`` with an inverted ``Content-Range``."""
    try:
        suffix_len = int(end_s)
    except ValueError:
        return None
    if size == 0 or suffix_len <= 0:
        raise _UnsatisfiableRange("unsatisfiable suffix range")
    return max(0, size - suffix_len), size - 1


def _parse_bounded_range(start_s: str, end_s: str, size: int) -> tuple[int, int] | None:
    """Parse the ``bytes=start-`` / ``bytes=start-end`` forms against ``size``."""
    try:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or start > end:
        raise _UnsatisfiableRange("unsatisfiable range")
    return start, min(end, size - 1)


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single-range ``Range: bytes=start-end`` header against ``size``.

    Returns an inclusive ``(start, end)`` byte range, or ``None`` if there is no
    ``Range`` header, it names a unit other than ``bytes``, it requests more than
    one range, or it is otherwise malformed — RFC 9110 §14.2 lets a server ignore
    any Range request it does not want to honor, and falling back to a full
    ``200`` response is always a valid (if less efficient) answer. Raises
    :class:`_UnsatisfiableRange` only for a syntactically well-formed but
    unsatisfiable range, so the caller can answer that case with a clean ``416``
    instead of silently serving the wrong bytes.
    """
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :]
    if "," in spec or "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        return _parse_suffix_range(end_s, size)
    return _parse_bounded_range(start_s, end_s, size)


#
# `_safe_filename` and `_decode_id` are re-exported aliases of the pure parsers
# in `ledger.parsing` (FIX-09) so every existing call site in this module keeps
# working unchanged; the implementations and their property tests now live with
# the rest of the hand-rolled parsers in `ledger/parsing.py` /
# `tests/test_parsing_fuzz.py`.


# Named subjects a contributor may declare per submission (RM12/EXP-04). Bounded so a
# crafted form cannot ask the server to mint an unbounded number of tokens.
_MAX_NAMED_SUBJECTS: int = 20


def _named_subjects_count(raw: str) -> int:
    """Parse the ``named_subjects_count`` form field to a bounded int in 0..20.

    A blank, non-integer, or out-of-range value degrades to a safe count rather than
    raising, so a malformed form never takes the submit path down: a negative clamps
    to 0 and an oversized count clamps to :data:`_MAX_NAMED_SUBJECTS`.
    """
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError):
        return 0
    return max(0, min(value, _MAX_NAMED_SUBJECTS))


def _parse_response_days(text: str) -> int:
    """Extract a bare day count from a response-time string, or 0 if there is none.

    Matches a whole-string ``"7"`` or ``"7 days"`` (the machine-readable case) so a
    numeric window configured as free-text still yields a due date, while a real
    sentence — which states no single parseable window — yields 0 (leave empty).
    """
    match = re.fullmatch(r"\s*(\d+)\s*(?:days?)?\s*", text or "")
    return int(match.group(1)) if match else 0


# --- server construction ----------------------------------------------------


def make_server(
    archive: Archive,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    grants_path: Path | None = None,
    revocations_path: Path | None = None,
    allow_contributions: bool = False,
) -> http.server.HTTPServer:
    """Build (but do not start) the browse server bound to ``archive``.

    Binds to ``127.0.0.1`` by default rather than ``0.0.0.0`` so a freshly stood-up
    archive is reachable only from the local box until an operator deliberately
    exposes it behind a vetted reverse proxy (securability — do not bind the world
    by default). The pre-provisioned grants mapping is loaded once from
    ``grants_path`` (an absent file yields no grants, so everyone is anonymous —
    deny by default) and attached to the server, where the handler reads it.

    The revocation list lives at ``revocations_path`` when given, else at a
    ``revocations.json`` sitting beside the grants file. Its *path* — not a
    snapshot of its contents — is attached to the server, and the handler re-reads
    it on each authenticated request, so ``ledger grant revoke`` takes effect
    immediately with no restart (an absent file yields an empty set — nothing
    revoked). It is still read once here so a malformed file fails the server at
    startup rather than surfacing later as silent denials (fail fast).

    Dependencies are attached to the server instance rather than to module
    globals, so several archives can be served from one process without
    interfering (modularity, testability).
    """
    grants = load_grants(grants_path) if grants_path is not None else {}
    if revocations_path is None and grants_path is not None:
        revocations_path = grants_path.parent / "revocations.json"
    if revocations_path is not None:
        load_revocations(revocations_path)  # fail fast on an unreadable/malformed list
    # Threaded so the per-request response-time floor (which equalizes the timing of
    # a not-found vs a not-authorized record) never serializes other requests
    # (availability, responsiveness) — and so the site serves several readers at once.
    httpd = http.server.ThreadingHTTPServer((host, port), ArchiveRequestHandler)
    # Attach the dependencies the handler reads per request.
    httpd.archive = archive  # type: ignore[attr-defined]
    httpd.grants = grants  # type: ignore[attr-defined]
    httpd.revocations_path = revocations_path  # type: ignore[attr-defined]
    httpd.allow_contributions = allow_contributions  # type: ignore[attr-defined]
    return httpd


def serve(
    archive: Archive,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    grants_path: Path | None = None,
    revocations_path: Path | None = None,
    allow_contributions: bool = False,
) -> None:
    """Build and run the browse server until interrupted (blocking).

    A convenience over :func:`make_server` for a CLI or a ``python -m`` entry
    point. Binds to loopback by default (securability) and shuts down cleanly on
    ``KeyboardInterrupt`` so a steward can stop it without a traceback (usability).
    """
    httpd = make_server(
        archive,
        host=host,
        port=port,
        grants_path=grants_path,
        revocations_path=revocations_path,
        allow_contributions=allow_contributions,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        pass
    finally:
        httpd.server_close()
