# File: continuum/orchestrator/state.py
# UPDATED: M1 — added approval fields + developer sub-agent roles

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    BSA          = "bsa"
    ARCHITECT    = "architect"
    PLANNER      = "planner"
    DEVELOPER    = "developer"
    # M1: developer split into three specialised sub-agents (T3)
    DATABASE     = "database"   # runs first — migrations before API
    BACKEND      = "backend"    # REST API layer
    FRONTEND     = "frontend"   # SPA / UI layer (runs last)
    SECURITY     = "security"
    CODE_REVIEW  = "code_review"
    PR_REVIEW    = "pr_review"
    TEST         = "test"
    INFRA        = "infra"
    MEMORY       = "memory"


@dataclass
class AgentMessage:
    agent: AgentRole
    timestamp: float
    content: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GateStatus:
    name: str    # "local_verify", "security_sast", "pr_review", "tests"
    status: str  # "green", "red", "pending"
    retry_count: int = 0
    error_message: Optional[str] = None
    last_checked: Optional[float] = None


@dataclass
class ContinuumState:
    # Input
    request: str

    # Orchestration
    current_agent: Optional[AgentRole] = None
    next_agent: Optional[AgentRole] = None

    # Control
    gates: List[GateStatus] = field(default_factory=list)
    messages: List[AgentMessage] = field(default_factory=list)

    # Rule 8: Plans are contracts (PEV loop)
    pev_contract: Optional[Dict[str, Any]] = None  # PEVContract.to_dict()

    # Artifacts
    story: Optional[Dict[str, Any]] = None   # {"id", "title", "acceptance_criteria", …}
    contract: Optional[str] = None           # OpenAPI YAML string
    schema: Optional[str] = None            # SQL DDL string
    dag: Optional[Dict[str, Any]] = None    # Neo4j DAG structure
    code: Optional[Dict[str, str]] = None   # {"file_path": content}
    test_results: Optional[Dict[str, Any]] = None
    pr_url: Optional[str] = None

    # State flags
    human_approval_pending: bool = False
    approval_gate_name: Optional[str] = None
    error_message: Optional[str] = None

    # M1: human approval checkpoints (interrupt() gates)
    story_approved:  bool = False   # True after human approves BSA story
    design_approved: bool = False   # True after human approves Architect design
    merge_approved:  bool = False   # True after human approves the final PR

    # Metadata
    run_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
