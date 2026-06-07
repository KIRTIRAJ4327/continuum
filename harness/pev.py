# File: continuum/harness/pev.py
# MOVED FROM orchestrator/pev.py
# Rule 8: Plans are contracts — every agent action follows Plan→Execute→Verify

"""
Plan → Execute → Verify (PEV) loop.
Every agent action follows this cycle to enforce Rule 8: "Plans are contracts."

The PEV loop turns model intentions into bounded, observable, revisable state transitions.
- PLAN: Agent declares what it will touch (contract)
- EXECUTE: Run in sandbox with permission constraints
- VERIFY: Deterministic sensors (lint, type, test, SAST) validate the transition
"""

from typing import Dict, Any, Optional, Tuple
import time

class PEVContract:
    """
    A contract over the next state transition (Rule 8).
    
    This is created in the PLAN phase and checked in the VERIFY phase.
    It makes the agent's intentions explicit and machine-checkable.
    """
    
    def __init__(self, agent: str, objective: str):
        """
        Initialize a contract.
        
        Args:
            agent: Agent name (e.g., "developer")
            objective: What the agent intends to accomplish
        """
        self.agent = agent
        self.objective = objective
        self.files_to_touch: list = []
        self.expected_invariants: list = []
        self.validation_commands: list = []
        self.rollback_points: list = []
        self.risk_tier: str = "low"  # low / medium / high
        self.created_at: float = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for Neo4j storage."""
        return {
            "agent": self.agent,
            "objective": self.objective,
            "files_to_touch": self.files_to_touch,
            "expected_invariants": self.expected_invariants,
            "validation_commands": self.validation_commands,
            "rollback_points": self.rollback_points,
            "risk_tier": self.risk_tier,
            "created_at": self.created_at,
        }

async def plan_phase(agent_request: str, llm_client: Any) -> PEVContract:
    """
    PLAN phase: Agent declares its contract before executing.
    
    The agent (via LLM) generates:
    - Files it will touch
    - Expected invariants (preconditions)
    - Validation commands (how to verify success)
    - Rollback points (how to undo if needed)
    - Risk tier (low/medium/high)
    
    Args:
        agent_request: Structured brief to the agent
        llm_client: LLM client to call for contract generation
    
    Returns:
        PEVContract instance
    """
    # TODO: Call LLM with prompt:
    #   "Generate a contract for this work: {agent_request}"
    #   "Output JSON: {files_to_touch, expected_invariants, validation_commands, rollback_points, risk_tier}"
    # TODO: Parse LLM response into PEVContract
    
    contract = PEVContract(agent="unknown", objective="")
    return contract

async def execute_phase(contract: PEVContract, code: str, sandbox_client: Any) -> Dict[str, Any]:
    """
    EXECUTE phase: Run the agent's code in a sandbox with permission constraints.
    
    The sandbox isolates the code at a given permission tier:
    - read_only: no side effects
    - sandbox_edit: local patching, test execution
    - full_access: network, credentials (requires HITL gate)
    
    Args:
        contract: PEVContract from plan phase
        code: Python code to execute (agent-generated)
        sandbox_client: ACASessionClient or HyperlightClient
    
    Returns:
        {"stdout": str, "stderr": str, "exit_code": int, "files_changed": [...]}
    """
    # TODO: Submit code to sandbox_client.execute_code(code)
    # TODO: Track which files were modified
    # TODO: Return execution result
    
    return {
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "files_changed": []
    }

async def verify_phase(result: Dict[str, Any], contract: PEVContract, sensors: Dict[str, Any]) -> Tuple[bool, str]:
    """
    VERIFY phase: Run deterministic sensors to validate the transition.
    
    Sensors (in order, stopping at first failure):
    1. Lint (ruff)
    2. Type check (mypy)
    3. Unit tests (pytest)
    4. Build check
    5. SAST (security scan)
    6. Contract validation (does generated code match OpenAPI spec?)
    
    Returns:
        (passed: bool, report: str)
    """
    # TODO: Run each sensor in order
    # TODO: If any sensor fails, return (False, report)
    # TODO: If all pass, return (True, report)
    
    passed = True
    report = "All sensors passed"
    return (passed, report)
