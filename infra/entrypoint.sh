#!/bin/sh
# infra/entrypoint.sh — map the documented env vars onto ledger's CLI flags and
# start the browse server. The `ledger` CLI takes flags (--root, --name, --host,
# --port), not environment variables, for these, so this thin shim translates.
#
# Behaviour:
#   * If the archive at $LEDGER_ROOT has not been initialized yet (no
#     store/config.json), run `ledger init` once. This makes the first
#     `docker compose up` on an empty volume just work (operability).
#   * Then run `ledger serve` bound to 0.0.0.0 inside the container so the host
#     can reach it across the container boundary. The host is responsible for
#     publishing it on loopback and fronting it with a reverse proxy.
#
# `exec` is used so ledger becomes PID 1's child and receives signals directly,
# giving clean shutdown on `docker stop` (the server handles KeyboardInterrupt).
set -eu

LEDGER_ROOT="${LEDGER_ROOT:-/data}"
LEDGER_PORT="${LEDGER_PORT:-8000}"
LEDGER_ARCHIVE_NAME="${LEDGER_ARCHIVE_NAME:-community-archive}"

# Optional: a pre-provisioned grants file (read-only, mounted by the operator).
# An absent file means every viewer is anonymous (deny by default).
GRANTS_ARG=""
if [ -n "${LEDGER_GRANTS_PATH:-}" ] && [ -f "${LEDGER_GRANTS_PATH}" ]; then
    GRANTS_ARG="--grants ${LEDGER_GRANTS_PATH}"
fi

# Warn (do not fail) if no vault key is set: an archive can run without one until
# the first contributor identity is sealed, but sealing will fail without it.
if [ -z "${LEDGER_VAULT_KEY:-}" ]; then
    echo "ledger: warning — LEDGER_VAULT_KEY is not set; identity sealing will be unavailable until it is." >&2
fi

# Initialize the archive once, idempotently. The store config lives at
# $LEDGER_ROOT/store/config.json (see ledger.config.Config.default).
if [ ! -f "${LEDGER_ROOT}/store/config.json" ]; then
    echo "ledger: initializing new archive '${LEDGER_ARCHIVE_NAME}' at ${LEDGER_ROOT}" >&2
    ledger init --root "${LEDGER_ROOT}" --name "${LEDGER_ARCHIVE_NAME}"
fi

echo "ledger: serving '${LEDGER_ARCHIVE_NAME}' on 0.0.0.0:${LEDGER_PORT} (container-internal)" >&2
# shellcheck disable=SC2086  # GRANTS_ARG is intentionally word-split when present.
exec ledger serve --root "${LEDGER_ROOT}" --host 0.0.0.0 --port "${LEDGER_PORT}" ${GRANTS_ARG}
