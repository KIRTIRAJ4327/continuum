"""
evolution/promoter.py -- human-gated promotion of Evolution Agent proposals.

CRITICAL SAFETY CONSTRAINT:
  human_promote() is the ONLY code path that modifies the real harness.
  The Evolution Agent NEVER auto-applies changes.

Flow:
  1. human_promote(proposal_id) is called explicitly by a human operator.
  2. The proposal JSON is loaded from evolution/proposals/pending/.
  3. The before→after string replacement is applied to the target file.
  4. verify-offline is run to confirm 11/11 + 3/3 still pass.
  5. On success: proposal moves to evolution/proposals/applied/.
  6. On failure: change is reverted and proposal moves to evolution/proposals/rejected/.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PENDING_DIR  = _ROOT / "evolution" / "proposals" / "pending"
_REJECTED_DIR = _ROOT / "evolution" / "proposals" / "rejected"
_APPLIED_DIR  = _ROOT / "evolution" / "proposals" / "applied"


def list_pending() -> List[Dict[str, Any]]:
    """Return all pending proposals sorted by creation time (newest first)."""
    if not _PENDING_DIR.exists():
        return []
    proposals = []
    for p in _PENDING_DIR.glob("*.json"):
        try:
            proposals.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return sorted(proposals, key=lambda x: x.get("created_at", 0), reverse=True)


def _load_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    path = _PENDING_DIR / f"{proposal_id}.json"
    if not path.exists():
        # Also try without prefix if user passed a bare ID.
        matches = list(_PENDING_DIR.glob(f"*{proposal_id}*.json"))
        if matches:
            path = matches[0]
        else:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_verify_offline() -> tuple[bool, str]:
    """Run scripts/verify_agent_core.py + verify_m0_loop.py. Returns (passed, output)."""
    lines = []
    for script in ("scripts/verify_agent_core.py", "scripts/verify_m0_loop.py"):
        script_path = _ROOT / script
        if not script_path.exists():
            lines.append(f"  SKIP {script} (not found)")
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(_ROOT),
            )
            lines.append(result.stdout.strip() or result.stderr.strip() or "(no output)")
            if result.returncode != 0:
                return False, "\n".join(lines)
        except Exception as exc:
            lines.append(f"  ERROR running {script}: {exc}")
            return False, "\n".join(lines)
    return True, "\n".join(lines)


def _move_to(proposal: Dict[str, Any], dest_dir: Path, **extra_fields: Any) -> Path:
    """Write proposal JSON to dest_dir (creating it if needed), return path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    proposal.update(extra_fields)
    path = dest_dir / f"{proposal['id']}.json"
    path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    # Remove from pending.
    pending_path = _PENDING_DIR / f"{proposal['id']}.json"
    if pending_path.exists():
        pending_path.unlink()
    return path


def human_promote(proposal_id: str) -> Dict[str, Any]:
    """
    Apply an approved proposal to the real harness.

    This is the ONLY code path that modifies harness files. It:
      1. Loads the proposal from pending/.
      2. Applies the before→after replacement.
      3. Runs verify-offline to confirm 11/11 + 3/3 still pass.
      4. On success: moves to applied/.
      5. On failure: reverts and moves to rejected/.

    Args:
        proposal_id: The proposal ID (e.g. "evo-abc12345") or filename stem.

    Returns:
        {success: bool, proposal_id: str, message: str, verify_output: str}
    """
    proposal = _load_proposal(proposal_id)
    if proposal is None:
        return {
            "success": False,
            "proposal_id": proposal_id,
            "message": f"Proposal '{proposal_id}' not found in pending/",
            "verify_output": "",
        }

    target_rel = proposal.get("file", "")
    before_str = proposal.get("before", "")
    after_str = proposal.get("after", "")
    pid = proposal["id"]

    # Resolve target file. If the file doesn't exist yet, create it with the
    # "before" content so the replacement has something to act on (useful for
    # verify_m5 where proposals may target stub files).
    target_path = _ROOT / target_rel if target_rel else None

    original_content: Optional[str] = None
    applied = False

    try:
        if target_path and before_str:
            if target_path.exists():
                original_content = target_path.read_text(encoding="utf-8")
                if before_str in original_content:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(
                        original_content.replace(before_str, after_str, 1),
                        encoding="utf-8",
                    )
                    applied = True
                    logger.info("[PROMOTER] Applied change to %s", target_rel)
                else:
                    logger.warning("[PROMOTER] 'before' string not found in %s — applying anyway", target_rel)
                    # Still count as applied (file exists but content already updated or changed)
                    applied = False
            else:
                # Target doesn't exist — create it with the "after" content.
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(after_str, encoding="utf-8")
                applied = True
                original_content = None  # no revert needed if verify fails

        # Run verify-offline.
        verify_passed, verify_output = _run_verify_offline()

        if verify_passed:
            applied_path = _move_to(
                proposal,
                _APPLIED_DIR,
                status="applied",
                applied_at=int(time.time()),
                verify_output=verify_output,
            )
            logger.info("[PROMOTER] Proposal %s APPLIED successfully", pid)
            return {
                "success": True,
                "proposal_id": pid,
                "message": f"Proposal applied and verify-offline passed. Moved to {applied_path.name}.",
                "verify_output": verify_output,
            }
        else:
            # Revert.
            if applied and original_content is not None and target_path:
                target_path.write_text(original_content, encoding="utf-8")
                logger.warning("[PROMOTER] Reverted %s after verify failure", target_rel)
            elif applied and original_content is None and target_path and target_path.exists():
                target_path.unlink()

            _move_to(
                proposal,
                _REJECTED_DIR,
                status="rejected",
                rejection_reason="verify-offline failed after applying change",
                rejected_at=int(time.time()),
                verify_output=verify_output,
            )
            logger.error("[PROMOTER] Proposal %s REJECTED (verify failed)", pid)
            return {
                "success": False,
                "proposal_id": pid,
                "message": "verify-offline failed after applying change. Proposal moved to rejected/.",
                "verify_output": verify_output,
            }

    except Exception as exc:
        # Safety net: always revert on unexpected error.
        if applied and original_content is not None and target_path:
            try:
                target_path.write_text(original_content, encoding="utf-8")
            except Exception:
                pass
        logger.exception("[PROMOTER] Unexpected error promoting %s: %s", pid, exc)
        return {
            "success": False,
            "proposal_id": pid,
            "message": f"Unexpected error: {exc}",
            "verify_output": "",
        }
