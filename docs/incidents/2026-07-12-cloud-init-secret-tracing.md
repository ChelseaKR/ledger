# Incident: cloud-init traced synthetic demo secrets — 2026-07-12

**Severity:** SEV2

**Status:** Resolved

**Related issue:** [#86](https://github.com/ChelseaKR/ledger/issues/86)

## Summary

The first AWS showcase deployment ran its cloud-init shell with xtrace enabled.
Expanded assignments for the generated vault key and claim secret were written to
the IAM-restricted EC2 console log. The values were not published and the showcase
contained synthetic data only, but operational logs are not an acceptable secret
store. Both credentials were rotated, the synthetic archive was rebuilt, and
shell tracing was removed from provisioning.

## Timeline (UTC)

| Time | Event |
|---|---|
| 05:39 | Terraform apply began for the first AWS showcase deployment. |
| 05:41 | Cloud-init completed; health and TLS became ready. |
| 05:45 | Post-deploy console review detected traced secret assignments. |
| 05:47 | Both SSM values were rotated; the runtime environment was replaced; the synthetic archive volume was destroyed and reseeded. |
| 05:48 | Health returned 200 under the replacement credentials. |

## Impact

Two L3 capability values appeared in an IAM-restricted AWS operational log. No
real contributor or archive data existed on the instance, the values were never
committed or printed in the task transcript, and no public route exposed them.
Anyone with permission to read EC2 console output during the exposure window
could have read them. Rotation invalidated both values.

## Detection

The deployment smoke test included a manual EC2 console-output review after
cloud-init completed. That review searched for provisioning errors and noticed
the use of `set -x`; a redacted occurrence count confirmed both assignments were
present without reproducing their values.

## Root cause

The user-data template used the common debugging combination `set -euxo
pipefail`. That is safe only for scripts that never handle secrets. Later work
added SSM retrieval and runtime-environment generation without removing xtrace,
and no infrastructure regression test prohibited it. Code review focused on
Terraform state and file permissions but did not include the rendered execution
log as a disclosure surface.

## What went well

- Post-deploy review found the issue minutes after first boot.
- The deployment used synthetic data and IAM-restricted logs.
- SSM-backed values could be replaced without changing Terraform state.
- The archive volume was disposable and reseeded cleanly under the new vault key.

## What went poorly

- The original deployment review did not treat shell tracing as secret output.
- There was no automated guard against xtrace in secret-bearing provisioning.
- The initial stability watch began before console-output review completed.

## Action items

| Action | Owner | Due | Tracking issue |
|---|---|---|---|
| Rotate both exposed values and rebuild the synthetic archive | Chelsea Kelly-Reif | 2026-07-12 | [#86](https://github.com/ChelseaKR/ledger/issues/86) — complete |
| Remove xtrace from user data | Chelsea Kelly-Reif | 2026-07-12 | [#86](https://github.com/ChelseaKR/ledger/issues/86) — complete in hotfix |
| Add a regression test forbidding xtrace in the template | Chelsea Kelly-Reif | 2026-07-12 | [#86](https://github.com/ChelseaKR/ledger/issues/86) — complete in hotfix |
| Include redacted console-log review before post-deploy stability monitoring | Chelsea Kelly-Reif | next deploy | [#86](https://github.com/ChelseaKR/ledger/issues/86) |

## Related links

- Incident issue: [#86](https://github.com/ChelseaKR/ledger/issues/86)
- Provisioning template: `infra/aws/terraform/user_data.sh.tftpl`
- Secret response procedure: [`docs/INCIDENT-RESPONSE.md`](../INCIDENT-RESPONSE.md)
