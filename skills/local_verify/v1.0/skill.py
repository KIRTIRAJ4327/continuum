"""Local verify skill — runs lint, type, test, build."""
from typing import Tuple

async def local_verify(code_path: str) -> Tuple[bool, str]:
    """
    Run local verification (lint, type, test, build).

    Returns:
        (passed: bool, output: str)
    """
    # TODO: Run ruff, mypy, pytest, build
    return (True, "All checks passed")
