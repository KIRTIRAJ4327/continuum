"""Unit test sensor (pytest)."""
import subprocess
from typing import Tuple

async def run_tests(test_path: str = "tests/") -> Tuple[bool, str]:
    """Run pytest unit tests."""
    result = subprocess.run(["pytest", test_path, "-v"], capture_output=True, text=True)
    return (result.returncode == 0, result.stdout + result.stderr)
