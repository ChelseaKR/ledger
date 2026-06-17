#!/usr/bin/env bash
# destroy.sh — tear down the ledger AWS demo and stop all charges.
#
# Usage:
#   infra/aws/destroy.sh                 # interactive destroy (prompts to confirm)
#   infra/aws/destroy.sh -auto-approve   # any extra args pass through to `destroy`
set -euo pipefail

# --- locate the repo root from this script's location -----------------------
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)
cd -- "${REPO_ROOT}"

TF_DIR="infra/aws/terraform"

echo "==> ledger demo destroy"
echo "    repo root: ${REPO_ROOT}"
echo "    This removes the EC2 instance, the elastic IP, the S3 source bucket,"
echo "    and the SSM secrets (vault key + claim secret), stopping all charges."

terraform -chdir="${TF_DIR}" destroy "$@"

echo "==> destroy complete. All demo resources are gone; charges have stopped."
