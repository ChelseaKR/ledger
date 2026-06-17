# infra/Dockerfile — a minimal, self-hostable image for ledger.
#
# Goal (affordability, self-sustainability): a community can run the whole public
# archive on one inexpensive box with no hosted or cloud dependency. The image
# carries only what `ledger serve` needs — the package and its single runtime
# dependency (`cryptography`) — and runs as an unprivileged user.
#
# Pin the base by tag here for a readable, reproducible build. For production you
# should pin by *digest* so the base cannot change under you between builds, e.g.:
#   FROM python:3.12-slim@sha256:<digest>
# Look up the current digest with `docker buildx imagetools inspect python:3.12-slim`
# and commit it. The tag below is the floor; the digest is the contract.
FROM python:3.12-slim

# Fail fast and loud, and keep Python from buffering logs or writing .pyc files
# (observability — log lines appear immediately under a process supervisor).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is needed only by the HEALTHCHECK below. Install it, then drop the apt
# lists so they do not bloat the image or go stale (smaller attack surface).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# A dedicated, unprivileged account. Nothing in this image needs root at runtime;
# running as a non-root user limits the blast radius of any compromise
# (securability — least privilege). UID/GID 10001 is a high, fixed value so a
# host bind-mount can be chowned to match if an operator chooses to use one.
RUN groupadd --gid 10001 ledger \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ledger

WORKDIR /app

# Install the package itself. We copy the project metadata and sources and do a
# regular (non-editable) install so the `ledger` console script lands on PATH and
# the image does not depend on the source tree staying mounted. The build context
# for this Dockerfile is the repository root (see docker-compose.yml `build`).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# The browse server serves the bundled, framework-free web assets (web/static),
# resolved relative to the installed package's parent. Ship them in the image.
COPY web ./web
RUN pip install . \
    && rm -rf /root/.cache

# The archive lives under /data: the store (bags, records, logs, config.json) and
# the encrypted identity vault. This is a mount point for the named volume defined
# in docker-compose.yml so the data survives container restarts and rebuilds.
# It holds contributor records and (encrypted) identity material — see the volume
# warning in docker-compose.yml. Owned by the unprivileged user.
RUN mkdir -p /data && chown -R ledger:ledger /data /app
VOLUME ["/data"]

USER ledger

# The vault key is supplied at runtime via the LEDGER_VAULT_KEY environment
# variable (sourced from a .env file by compose), never baked into the image and
# never written to disk. See infra/.env.example.
ENV LEDGER_ROOT=/data \
    LEDGER_PORT=8000

EXPOSE 8000

# Bind to 0.0.0.0 *inside the container only*. The container is on an isolated
# bridge network; the host should publish it on loopback and front it with a
# vetted reverse proxy that terminates TLS and adds rate limiting/auth. Do NOT
# expose this port directly to the public internet. (The application itself
# defaults to 127.0.0.1 for exactly this reason; 0.0.0.0 here is so the host can
# reach the process across the container boundary, not so the world can.)
#
# `ledger serve` needs an initialized archive at --root. The entrypoint below
# runs `ledger init` once if the archive does not yet exist, then serves it, so a
# first `docker compose up` on an empty volume just works (operability).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LEDGER_PORT}/healthz" || exit 1

# Use a small shell entrypoint so LEDGER_ARCHIVE_NAME / LEDGER_PORT / LEDGER_ROOT
# env vars map onto the CLI's flags (the CLI takes flags, not env, for these).
COPY infra/entrypoint.sh /usr/local/bin/ledger-entrypoint
ENTRYPOINT ["/usr/local/bin/ledger-entrypoint"]
