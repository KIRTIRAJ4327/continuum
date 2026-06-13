# File: continuum/orchestrator/graph.py
# M1: AsyncPostgresSaver (langgraph-checkpoint-postgres), interrupt() for human gates

"""
LangGraph supervisor orchestrator for Continuum.
Handles routing, gating, retry logic, and state management.
"""
import logging
import time
from typing import Callable, Optional

from langgraph.graph import StateGraph, START, END

from .state import ContinuumState, AgentRole
from .agent_runner import AgentContext, run_agent

logger = logging.getLogger(__name__)


class ContinuumGraph:
    """LangGraph supervisor orchestrator."""

    def __init__(self, checkpointer_uri: str):
        """
        Initialize the orchestrator graph.

        Args:
            checkpointer_uri: PostgreSQL connection string for durable checkpoints.
                              Used by AsyncPostgresSaver (psycopg3-based).
        """
        self._checkpointer_uri = checkpointer_uri
        self.checkpointer = None   # set in async_init()
        self.context = AgentContext.from_env()
        self.graph = None          # set in async_init()

    @classmethod
    async def create(cls, checkpointer_uri: str) -> "ContinuumGraph":
        """Async factory — call instead of __init__ when running under asyncio."""
        instance = cls(checkpointer_uri)
        await instance.async_init()
        return instance

    async def async_init(self) -> None:
        """Set up the async checkpointer and compile the graph."""
        try:
            import psycopg
            from langgraph_checkpoint_postgres import AsyncPostgresSaver

            conn = await psycopg.AsyncConnection.connect(self._checkpointer_uri)
            self.checkpointer = AsyncPostgresSaver(conn)
            await self.checkpointer.setup()   # type: ignore[attr-defined]
        except ImportError:
            # langgraph-checkpoint-postgres not installed; run without checkpointing
            logger.warning(
                "langgraph-checkpoint-postgres not installed; "
                "running without durable checkpointing"
            )
            self.checkpointer = None

        self.graph = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph supervisor graph."""
        graph = StateGraph(ContinuumState)

        # Nodes
        graph.add_node("orchestrator", self._route)
        # Each agent node is a closure bound to its role so the runner knows
        # which agent it is (LangGraph nodes only receive `state`).
        for role in [
            "bsa", "architect", "planner",
            # M1 T3: developer split
            "developer", "database", "backend", "frontend",
            "security", "code_review", "pr_review", "test",
        ]:
            graph.add_node(role, self._make_runner(role))
        graph.add_node("human_gate", self._human_gate_node)
        graph.add_node("finish", self._finish)

        # Edges
        graph.add_edge(START, "orchestrator")

        # Conditional routing from orchestrator
        graph.add_conditional_edges(
            "orchestrator",
            self._next_node,
            {
                "bsa": "bsa",
                "architect": "architect",
                "planner": "planner",
                "developer": "developer",
                # M1 T3: dev sub-agents
                "database": "database",
                "backend": "backend",
                "frontend": "frontend",
                "security": "security",
                "code_review": "code_review",
                "pr_review": "pr_review",
                "test": "test",
                "human_gate": "human_gate",
                "finish": "finish",
            }
        )

        # All agent nodes route back to orchestrator
        for agent in [
            "bsa", "architect", "planner",
            "developer", "database", "backend", "frontend",
            "security", "code_review", "pr_review", "test",
        ]:
            graph.add_edge(agent, "orchestrator")

        # Human gate resumes back to orchestrator after interrupt() is resolved
        graph.add_edge("human_gate", "orchestrator")
        graph.add_edge("finish", END)

        return graph.compile(checkpointer=self.checkpointer)

    # ---------------------------------------------------------------------- #
    # Orchestrator (routing) node
    # ---------------------------------------------------------------------- #
    async def _route(self, state: ContinuumState) -> ContinuumState:
        """
        ORCHESTRATOR NODE: Decides which agent to run next.

        PRD §4 rules enforced:
        - Rule 2: Every stage is a gate (check gate status before routing)
        - Rule 4: Max 3 retries per gate (GateStatus.retry_count)
        - Rule 9: No work tools — only route/finish

        M1 human gates (interrupt() checkpoints):
        - story_review  : after BSA — operator approves user story before design
        - design_review : after Architect — operator approves architecture before dev
        - local_verify  : after Developer (3 retries) — escalate persistent failures
        - merge_review  : after Security — operator approves final PR before merge
        """
        logger.info("[ORCHESTRATOR] Routing decision for run %s", state.run_id)

        # If a previous interrupt() hasn't been resolved yet, re-enter the gate.
        if state.human_approval_pending:
            logger.info("  -> Human approval still pending at gate '%s'", state.approval_gate_name)
            state.next_agent = None  # _next_node returns "human_gate"
            return state

        if state.current_agent is None:
            logger.info("  -> First run: routing to BSA")
            state.next_agent = AgentRole.BSA

        elif state.current_agent == AgentRole.BSA:
            if not state.story_approved:
                logger.info("  -> BSA done: awaiting story approval (story_review gate)")
                state.human_approval_pending = True
                state.approval_gate_name = "story_review"
                state.next_agent = None
            else:
                logger.info("  -> Story approved: routing to Architect")
                state.next_agent = AgentRole.ARCHITECT

        elif state.current_agent == AgentRole.ARCHITECT:
            if not state.design_approved:
                logger.info("  -> Architect done: awaiting design approval (design_review gate)")
                state.human_approval_pending = True
                state.approval_gate_name = "design_review"
                state.next_agent = None
            else:
                logger.info("  -> Design approved: routing to Planner")
                state.next_agent = AgentRole.PLANNER

        elif state.current_agent == AgentRole.PLANNER:
            # M1 T3: route Planner → DATABASE (first sub-agent in the dev chain)
            logger.info("  -> Planner done: routing to Database sub-agent")
            state.next_agent = AgentRole.DATABASE

        elif state.current_agent == AgentRole.DATABASE:
            logger.info("  -> Database done: routing to Backend sub-agent")
            state.next_agent = AgentRole.BACKEND

        elif state.current_agent == AgentRole.BACKEND:
            logger.info("  -> Backend done: routing to Frontend sub-agent")
            state.next_agent = AgentRole.FRONTEND

        elif state.current_agent in (AgentRole.DEVELOPER, AgentRole.FRONTEND):
            # local_verify runs after the last dev sub-agent (FRONTEND) or legacy DEVELOPER
            gate = next((g for g in state.gates if g.name == "local_verify"), None)
            if gate and gate.status == "red":
                if gate.retry_count < 3:
                    logger.warning(
                        "  -> local_verify FAILED (attempt %d/3), dev retries",
                        gate.retry_count + 1,
                    )
                    gate.retry_count += 1
                    # Retry by re-running the FRONTEND agent (it regenerates all code)
                    state.next_agent = (
                        AgentRole.FRONTEND
                        if state.current_agent == AgentRole.FRONTEND
                        else AgentRole.DEVELOPER
                    )
                else:
                    logger.error("  -> local_verify FAILED after 3 retries, escalating to human")
                    state.human_approval_pending = True
                    state.approval_gate_name = "local_verify"
                    state.next_agent = None
            elif gate and gate.status == "green":
                logger.info("  -> local_verify PASSED: routing to Security")
                state.next_agent = AgentRole.SECURITY
            else:
                # Gate not yet set
                state.next_agent = (
                    AgentRole.FRONTEND
                    if state.current_agent == AgentRole.FRONTEND
                    else AgentRole.DEVELOPER
                )

        elif state.current_agent == AgentRole.SECURITY:
            if not state.merge_approved:
                logger.info("  -> Security done: awaiting merge approval (merge_review gate)")
                state.human_approval_pending = True
                state.approval_gate_name = "merge_review"
                state.next_agent = None
            else:
                logger.info("  -> Merge approved: finishing")
                state.next_agent = None

        else:
            logger.info("  -> Default: finishing")
            state.next_agent = None

        logger.info(
            "  -> Next: %s", state.next_agent.value if state.next_agent else "FINISH"
        )
        return state

    # ---------------------------------------------------------------------- #
    # Human gate node — uses interrupt() for durable pause/resume (M1)
    # ---------------------------------------------------------------------- #
    async def _human_gate_node(self, state: ContinuumState) -> ContinuumState:
        """
        HUMAN GATE NODE: Pause execution and wait for a human decision.

        Uses langgraph.types.interrupt() so the run is durably suspended in
        the Postgres checkpointer and can be resumed via Command(resume=...).

        Gate types handled:
          story_review  — sets state.story_approved   = True on approval
          design_review — sets state.design_approved  = True on approval
          merge_review  — sets state.merge_approved   = True on approval
          local_verify  — resets the gate for one more retry on approval

        The interrupt payload is surfaced via GET /run/{id} to the operator.
        Rejection (approved=False) leaves human_approval_pending=True so
        GET /run/{id} shows "escalated" and no further routing occurs.
        """
        try:
            from langgraph.types import interrupt  # available in langgraph >=1.0

            gate_name = state.approval_gate_name or "unknown"
            message = _gate_message(gate_name)

            decision = interrupt({
                "gate": gate_name,
                "message": message,
                "run_id": state.run_id,
                "artifacts": _gate_artifacts(state, gate_name),
            })

            if not (decision and decision.get("approved")):
                # Rejected or no decision — leave pending so run stays "escalated"
                logger.warning("[HUMAN_GATE] Gate '%s' rejected or no decision; run escalated", gate_name)
                return state

            # --- Approved: update the relevant approval flag ---
            if gate_name == "story_review":
                state.story_approved = True
                logger.info("[HUMAN_GATE] Story approved — Architect will run next")

            elif gate_name == "design_review":
                state.design_approved = True
                logger.info("[HUMAN_GATE] Design approved — Planner will run next")

            elif gate_name == "merge_review":
                state.merge_approved = True
                logger.info("[HUMAN_GATE] Merge approved — pipeline finishing")

            else:
                # Gate-failure escalation (e.g., local_verify after 3 retries)
                # Approved = "give it one more chance"
                gate = next((g for g in state.gates if g.name == gate_name), None)
                if gate:
                    gate.retry_count = 0
                    gate.status = "pending"
                logger.info("[HUMAN_GATE] Gate '%s' reset for retry after human approval", gate_name)

            state.human_approval_pending = False
            state.approval_gate_name = None

        except ImportError:
            # langgraph not installed — fall back to flag-only (offline tests)
            logger.warning("interrupt() unavailable; human gate is a no-op in offline mode")

        return state

    # ---------------------------------------------------------------------- #
    # Agent runner nodes
    # ---------------------------------------------------------------------- #
    def _make_runner(self, agent: str) -> Callable:
        """Build a LangGraph node function bound to a specific agent role."""
        async def _node(state: ContinuumState) -> ContinuumState:
            return await self._run_agent(state, agent)
        _node.__name__ = f"run_{agent}"
        return _node

    async def _run_agent(self, state: ContinuumState, agent: str) -> ContinuumState:
        """Delegate to agent_runner.run_agent — load YAML, call LLM, bind skills."""
        return await run_agent(state, agent, self.context)

    # ---------------------------------------------------------------------- #
    # Conditional edge
    # ---------------------------------------------------------------------- #
    async def _next_node(self, state: ContinuumState) -> str:
        """Return the next node name based on state.next_agent."""
        if state.human_approval_pending:
            return "human_gate"
        if state.next_agent:
            return state.next_agent.value
        return "finish"

    # ---------------------------------------------------------------------- #
    # Finish node
    # ---------------------------------------------------------------------- #
    async def _finish(self, state: ContinuumState) -> ContinuumState:
        """Wrap up the run and record completion time."""
        state.completed_at = time.time()
        elapsed = state.completed_at - (state.started_at or state.completed_at)
        logger.info("[FINISH] Run %s complete in %.1fs", state.run_id, elapsed)
        return state

    # ---------------------------------------------------------------------- #
    # Public entry points
    # ---------------------------------------------------------------------- #
    async def run(self, request: str, thread_id: str) -> ContinuumState:
        """
        Execute the orchestrator graph for a feature request.

        Args:
            request:   Plain-English feature request.
            thread_id: Unique thread ID for durable resumption via checkpointer.

        Returns:
            Final ContinuumState with all artifacts.
        """
        if self.graph is None:
            raise RuntimeError("Call await ContinuumGraph.create() before run()")

        initial_state = ContinuumState(
            request=request,
            run_id=thread_id,
            started_at=time.time(),
        )
        logger.info("[START] Run %s: %s", thread_id, request)

        result = await self.graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
        return result

    async def resume(self, thread_id: str, approved: bool) -> Optional[ContinuumState]:
        """
        Resume a run that is suspended at a human_gate interrupt().

        Args:
            thread_id: The thread ID of the suspended run.
            approved:  True to approve the gate; False to reject (abort).

        Returns:
            Updated ContinuumState, or None if checkpointer is unavailable.
        """
        if self.graph is None:
            raise RuntimeError("Call await ContinuumGraph.create() before resume()")
        if self.checkpointer is None:
            logger.warning("No checkpointer available; resume() is a no-op")
            return None

        from langgraph.types import Command

        result = await self.graph.ainvoke(
            Command(resume={"approved": approved}),
            config={"configurable": {"thread_id": thread_id}},
        )
        return result


# --------------------------------------------------------------------------- #
# Gate message helpers
# --------------------------------------------------------------------------- #
def _gate_message(gate_name: str) -> str:
    messages = {
        "story_review": (
            "The BSA agent has completed the user story. "
            "Please review the story and acceptance criteria before design begins. "
            "Approve to proceed to Architect, or reject to escalate."
        ),
        "design_review": (
            "The Architect agent has produced the OpenAPI contract, database schema, "
            "and task DAG. Please review the architecture before development begins. "
            "Approve to proceed to Planner, or reject to escalate."
        ),
        "merge_review": (
            "Security scanning is complete. "
            "Please review the generated code and test results before merging. "
            "Approve to finalise the run, or reject to escalate."
        ),
    }
    return messages.get(
        gate_name,
        f"Gate '{gate_name}' requires human approval. Approve to continue, reject to abort.",
    )


def _gate_artifacts(state: ContinuumState, gate_name: str) -> dict:
    """Return the relevant artifacts for the operator to review at each gate."""
    if gate_name == "story_review":
        return {"story": state.story}
    if gate_name == "design_review":
        return {
            "contract_preview": (state.contract or "")[:800],
            "schema_preview": (state.schema or "")[:400],
            "dag_task_count": len((state.dag or {}).get("tasks", [])),
        }
    if gate_name == "merge_review":
        return {
            "code_files": list((state.code or {}).keys()),
            "gates": [
                {"name": g.name, "status": g.status} for g in state.gates
            ],
        }
    return {}
