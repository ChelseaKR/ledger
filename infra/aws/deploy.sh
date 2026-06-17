#!/usr/bin/env bash
# deploy.sh — stand up (or update) the ledger AWS demo.
#
# This ships the COMMITTED tree: it builds a `git archive` bundle from HEAD,
# which Terraform uploads to S3 and the instance builds the image from on first
# boot. Uncommitted changes are NOT deployed. SYNTHETIC data only — this is a
# public showcase, never a real archive.
#
# Usage:
#   infra/aws/deploy.sh                 # interactive apply (prompts to confirm)
#   infra/aws/deploy.sh -auto-approve   # any extra args pass through to `apply`
set -euo pipefail

# --- locate the repo root from this script's location -----------------------
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
cd -- "${REPO_ROOT}"

TF_DIR="infra/aws/terraform"
BUILD_DIR="${TF_DIR}/.build"
BUNDLE="${BUILD_DIR}/source.zip"
TFVARS="${TF_DIR}/terraform.tfvars"
TFVARS_EXAMPLE="${TF_DIR}/terraform.tfvars.example"

echo "==> ledger demo deploy"
echo "    repo root: ${REPO_ROOT}"

# --- warn (do not abort) if the working tree is dirty -----------------------
# The deploy ships the committed tree, so uncommitted edits will be invisible.
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: working tree is not clean. The deploy ships the COMMITTED tree (HEAD)," >&2
  echo "         so any uncommitted changes below will NOT be deployed:" >&2
  git status --short >&2
  echo "         Commit them first if you want them included." >&2
fi

# --- build the source bundle Terraform expects ------------------------------
echo "==> building source bundle: ${BUNDLE}"
mkdir -p "${BUILD_DIR}"
git archive --format=zip -o "${BUNDLE}" HEAD
echo "    wrote $(du -h "${BUNDLE}" | cut -f1) bundle from HEAD ($(git rev-parse --short HEAD))"

# --- require terraform.tfvars -----------------------------------------------
if [ ! -f "${TFVARS}" ]; then
  echo "ERROR: ${TFVARS} not found." >&2
  echo "       Create it from the example and set your domain + ACME email:" >&2
  echo "         cp ${TFVARS_EXAMPLE} ${TFVARS}" >&2
  echo "         \$EDITOR ${TFVARS}" >&2
  exit 1
fi

# --- terraform init + apply -------------------------------------------------
echo "==> terraform init"
terraform -chdir="${TF_DIR}" init -input=false

echo "==> terraform apply"
terraform -chdir="${TF_DIR}" apply "$@"

# --- next steps -------------------------------------------------------------
cat <<'EOF'

==> apply complete.

Next steps:
  1. Unless you set route53_zone_id, point your domain's A record at the
     `elastic_ip` output (TTL 300):
         terraform -chdir=infra/aws/terraform output elastic_ip
  2. Wait ~1 minute after DNS resolves for Caddy to obtain the TLS certificate.
  3. Visit the site at the `url` output:
         terraform -chdir=infra/aws/terraform output url

  See `terraform -chdir=infra/aws/terraform output dns_instructions` and
  `... output logs_hint` for details and first-boot logs.
EOF
