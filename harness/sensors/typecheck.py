"""Type checking sensor (mypy)."""
import subprocess
from typing import Tuple

async def run_typecheck(code_path: str) -> Tuple[bool, str]:
    """Run mypy type check."""
    result = subprocess.run(["mypy", code_path, "--ignore-missing-imports"], capture_output=True, text=True)
    return (result.returncode == 0, result.stdout + result.stderr)
