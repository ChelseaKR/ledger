# Incident postmortems

No ledger incident has been recorded as of 2026-07-11. New SEV1–3 incidents use
`YYYY-MM-DD-<slug>.md` and the template below. Reports are blameless, redact-safe,
and committed before the incident issue closes.

```markdown
# Incident: <summary> — YYYY-MM-DD

**Severity:** SEV1–4
**Status:** Resolved / Monitoring / Postmortem-only
**Related issue:** #NN

## Summary
## Timeline (UTC)
## Impact
## Detection
## Root cause
## What went well
## What went poorly
## Action items

| Action | Owner | Due | Tracking issue |
|---|---|---|---|

## Related links
```

The Impact section must state whether L2/L3 data was exposed. Never include real
identity, sealed content, or credential values.
