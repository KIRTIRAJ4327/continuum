# File: continuum/harness/__init__.py

"""
Harness layer — Plan→Execute→Verify loop, deterministic sensors, permission enforcement.

This module enforces Rules 1, 3, 4, and 8 from the PRD:
- Rule 1: Never push and pray (local_verify gate)
- Rule 3: Verify locally what CI verifies remotely (ci-mirror docker image)
- Rule 4: Bound the auto-fix loop (max 3 retries per gate)
- Rule 8: Plans are contracts (PEV loop)
"""

from .pev import PEVContract, plan_phase, execute_phase, verify_phase
from .permissions import PermissionTier, PermissionChecker

__all__ = [
    "PEVContract",
    "plan_phase",
    "execute_phase",
    "verify_phase",
    "PermissionTier",
    "PermissionChecker",
]
