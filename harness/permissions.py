# File: continuum/harness/permissions.py
# MOVED FROM orchestrator/permissions.py
# Rule 4.2: Three-tier permission model enforced by the harness

"""
Three-tier permission model enforcement (PRD §4.2, Rule 8).

Permission tiers:
- READ_ONLY: Read repo, query Neo4j/Search (no side effects)
- SANDBOX_EDIT: Patch files, run tests/builds in isolated workspace (sandbox only)
- FULL_ACCESS: Network, credentials, deployment, destructive ops (HITL gate required)
"""

from enum import Enum
from typing import Optional

class PermissionTier(str, Enum):
    """Permission tiers for agent operations."""
    READ_ONLY = "read_only"
    SANDBOX_EDIT = "sandbox_edit"
    FULL_ACCESS = "full_access"

class PermissionChecker:
    """
    Enforce 3-tier permissions (PRD §4.2).
    
    Every agent is assigned a tier. The harness enforces which operations they can perform.
    Tier-3 (full_access) operations require a mandatory human gate.
    """
    
    def __init__(self):
        """Initialize permission checker with default agent mappings."""
        self.agent_tiers = {
            "orchestrator": PermissionTier.READ_ONLY,  # No work tools
            "bsa": PermissionTier.SANDBOX_EDIT,
            "architect": PermissionTier.SANDBOX_EDIT,
            "planner": PermissionTier.READ_ONLY,  # Only reads DAG
            "developer": PermissionTier.SANDBOX_EDIT,  # Code runs in sandbox
            "security": PermissionTier.SANDBOX_EDIT,  # SAST runs in sandbox
            "code_review": PermissionTier.SANDBOX_EDIT,  # Raises PR (side effect)
            "pr_review": PermissionTier.SANDBOX_EDIT,  # Approves PR
            "test": PermissionTier.SANDBOX_EDIT,  # Tests run in sandbox
        }
        
        # Tier-3 operations require human approval
        self.tier_3_operations = [
            "deploy",
            "delete_branch",
            "merge_without_approval",
            "provision_infrastructure",
            "access_credentials",
        ]
    
    def can_access(self, agent: str, operation: str, current_tier: PermissionTier) -> bool:
        """
        Check if an agent can perform an operation at a given tier.
        
        Args:
            agent: Agent name
            operation: Operation (e.g., "write_file", "deploy")
            current_tier: Requested tier
        
        Returns:
            bool: True if allowed, False otherwise
        """
        # Get agent's assigned tier
        agent_tier = self.agent_tiers.get(agent)
        if not agent_tier:
            return False
        
        # Tier escalation check
        if current_tier == PermissionTier.FULL_ACCESS:
            # Tier-3 always requires explicit HITL gate
            # This should be checked at graph level, not here
            return False
        
        if current_tier == PermissionTier.SANDBOX_EDIT:
            # Agent must be at least sandbox_edit tier
            return agent_tier in [
                PermissionTier.SANDBOX_EDIT,
                PermissionTier.FULL_ACCESS
            ]
        
        if current_tier == PermissionTier.READ_ONLY:
            # All agents can read
            return True
        
        return False
    
    def requires_human_gate(self, operation: str) -> bool:
        """Check if an operation requires a human approval gate."""
        return operation in self.tier_3_operations
