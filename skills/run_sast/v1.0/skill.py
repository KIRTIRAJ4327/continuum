"""Run SAST skill — security scanning."""
from typing import Dict, Any

async def run_sast(code_path: str) -> Dict[str, Any]:
    """
    Run SAST scanner on the code.

    Returns:
        {"issues": [...], "clean": bool}
    """
    # TODO: Call SAST tool (Bandit, Semgrep, etc.)
    return {"issues": [], "clean": True}
