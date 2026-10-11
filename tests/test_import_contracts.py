"""The module map holds (metalm guidelines/design.md#the-package-docstring)."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_import_contracts_hold():
    out = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "lint-imports")], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout + out.stderr
