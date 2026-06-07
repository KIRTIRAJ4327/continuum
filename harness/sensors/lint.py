"""Linting sensor (ruff)."""
import subprocess
from typing import Tuple

async def run_lint(code_path: str) -> Tuple[bool, str]:
    """Run ruff lint check."""
    result = subprocess.run(["ruff", "check", code_path], capture_output=True, text=True)
    return (result.returncode == 0, result.stdout + result.stderr)
