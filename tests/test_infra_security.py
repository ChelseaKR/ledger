"""Security regressions for deployment infrastructure."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_DATA = ROOT / "infra" / "aws" / "terraform" / "user_data.sh.tftpl"


def test_cloud_init_never_enables_shell_tracing() -> None:
    """Provisioning must not echo expanded secret assignments to console logs."""
    script = USER_DATA.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )

    assert not re.search(r"(?:^|\n)\s*set\s+-[^\n\s]*x", executable)
    assert not re.search(r"(?:^|\n)\s*set\s+-o\s+xtrace\b", executable)
    assert "set -euo pipefail" in executable
    assert "VAULT_KEY=$(get_or_create" in executable
    assert "CLAIM_SECRET=$(get_or_create" in executable
