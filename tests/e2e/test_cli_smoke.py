from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_cli_precheck_smoke() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = "."
    result = subprocess.run(
        [sys.executable, "cli/main.py", "precheck", "examples/bioinfo_python_case/veritas.json"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Verification Level Preview" in result.stdout
