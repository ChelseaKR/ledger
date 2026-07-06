# 7. Withhold-and-acknowledge instead of 403 for unauthorized access

## Status

Accepted

## Context

SECURITY-AND-SUPPLY-CHAIN-STANDARD's ASVS V4 function-level-authorization control
(SEC-21) is normally read as "an unauthorized request gets an explicit `403`." A
2026-07-05 conformance audit flagged that ledger does not do this, and asked
whether the divergence is a defect or a deliberate, defensible design choice that
was simply undocumented (SEC-21: "authz enforced server-side at one choke point
and heavily tested, but ledger deliberately withholds rather than 403s...
undocumented — needs an ADR").

It is deliberate. ledger's central hard rule is that **holding a record must never
out its contributor**, and that guarantee extends to the record's *existence*, not
just its content. A sealed record is one whose existence itself may need to stay
invisible to an unauthorized viewer — a diary entry naming a person who is not out,
a mutual-aid runbook a hostile actor is looking for, a record whose mere
presence in a search result or a 403 response could confirm "this archive holds
something about X." A `403 Forbidden` is an *oracle*: it tells an unauthorized
requester that something exists at that address, gated behind a policy they don't
meet. For most ASVS-scoped applications that leak is acceptable or even desirable
(a clear signal to a legitimate user that they need different credentials). For
ledger it is the exact leak the no-outing rule exists to prevent — the same class
of problem as returning different errors for "wrong password" versus "no such
account."

## Decision

Unauthorized access to a sealed or restricted record or field is **withheld, not
403'd**: the response looks byte-for-byte like the response to a record that does
not exist, or a record with that field simply absent, depending on the surface:

- **Existence-sealed records** (`is_listable` in `src/ledger/access/policy.py`) are
  omitted entirely from browse, search, and list responses to an unauthorized
  viewer — the same shape as if the record were never ingested. There is no
  distinguishable "record exists but you can't see it" response.
- **Field-sealed values** on an otherwise-visible record (`disclose` in the same
  module) are omitted from the record's rendering — the field key may or may not
  appear depending on the schema, but the value never does, and the reading room
  states honestly that something is withheld and, where applicable, when it opens
  (`_render_withheld_state`-style copy — an honest "sealed until <date>" or "sealed
  from everyone, including stewards," never a bare disappearance that could itself
  read as a bug to a legitimate user).
- **Enforcement is still server-side, at one choke point** — every read path
  (`server.py`'s HTML routes, the JSON record/list APIs, the CSV export) calls
  through `access/policy.py`; there is no client-trusted disclosure decision
  anywhere. This is ASVS V4's actual intent (authorization decisions are made and
  enforced server-side, consistently, not left to the client) — only the *response
  shape* for a denial diverges from a raw `403`.
- **Cross-principal behavior is tested, not assumed**:
  `tests/test_reading_room_enforcement.py` drives a real loopback server as
  anonymous, granted, and steward viewers and asserts a sentinel identity and
  sentinel embargoed/redacted field values are absent from every anonymous HTML,
  JSON, and CSV response, while the reading room still honestly communicates that
  something is withheld. `tests/test_no_outing.py` is the broader sentinel-tripwire
  suite (run as its own isolated, merge-blocking CI job, `no-outing-audit`) that the
  SECURITY standard itself names as the portfolio's exemplar for this control
  (SEC-05).

## Consequences

- **The design meets ASVS V4's intent, not its letter.** A reviewer checking for a
  literal `403` status code will not find one on a sealed route; this ADR is the
  record that the divergence is intentional, tested, and traces to a
  higher-priority requirement (anti-enumeration / no-outing) that the standard's
  own text elsewhere endorses as an exemplar (SEC-05, SEC-06).
- **No enumeration oracle.** An attacker cannot distinguish "this record doesn't
  exist," "this record exists but is sealed to you," or "you mistyped the URL" —
  all three look the same. This is the property the design is optimizing for.
- **A legitimate but under-privileged user gets a softer signal than a REST purist
  might expect.** A steward missing a grant sees an honest "sealed" state with an
  opening date where one exists, rather than a hard `403`; this is a deliberate
  usability/safety trade-off (transparency about *state*, silence about
  *existence*) rather than an oversight.
- **Anyone extending a read path must go through `access/policy.py`.** Adding a new
  surface that makes its own visibility decision instead of calling `is_listable`/
  `disclose` would reopen exactly the class of bug the no-outing suite exists to
  catch; this is called out in `CONTRIBUTING.md`'s no-outing guidance and enforced
  by the disclosure test marker.

### Alternatives considered

- **Literal `403 Forbidden` for any authenticated-but-unauthorized request.**
  Rejected: leaks existence, which is the oracle ledger is specifically built to
  deny. Acceptable for most ASVS-scoped web apps; wrong for a tool whose subject
  matter is "who is at risk if this information's existence is confirmed."
  Consider it a candidate `waivers.yml`-style permanent exception rather than a gap
  to close, since 403 for this route class would itself be a new safety defect.
- **`404 Not Found` uniformly instead of a withheld-state message.** Considered as
  closer to a conventional anti-enumeration pattern (GitHub private repos do this).
  Rejected in this specific case for *field*-level withholding on a record the
  viewer can otherwise see: silently vanishing content confuses a legitimate viewer
  into thinking something broke, undermining usability and support-burden, while
  the record-level (existence) case does still behave like a plain 404-equivalent
  absence.
