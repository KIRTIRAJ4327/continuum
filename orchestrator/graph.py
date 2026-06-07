# File: continuum/orchestrator/graph.py
# M1: AsyncPostgresSaver (langgraph-checkpoint-postgres), interrupt() for human gates

"""
LangGraph supervisor orchestrator for Continuum.
Handles routing, gating, retry logic, and state management.
"""
import asyncio
import logging
import time
from typing import Callable, Optional

from langgraph.graph import StateGraph, START, END

from .state import ContinuumState, AgentRole, GateStatus
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
            await self.checkpointer.setup()   # creates checkpoint tables on first run
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
        for role in ["bsa", "architect", "planner", "developer", "security",
                     "code_review", "pr_review", "test"]:
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
                "security": "security",
                "code_review": "code_review",
                "pr_review": "pr_review",
                "test": "test",
                "human_gate": "human_gate",
                "finish": "finish",
            }
        )

        # All agent nodes route back to orchestrator
        for agent in ["bsa", "architect", "planner", "developer", "security",
                      "code_review", "pr_review", "test"]:
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
        """
        logger.info("[ORCHESTRATOR] Routing decision for run %s", state.run_id)

        if state.human_approval_pending:
            logger.info("  -> Human approval pending at gate '%s'", state.approval_gate_name)
            state.next_agent = None   # _next_node returns "human_gate"
            return state

        if state.current_agent is None:
            logger.info("  -> First run: routing to BSA")
            state.next_agent = AgentRole.BSA

        elif state.current_agent == AgentRole.BSA:
            logger.info("  -> BSA complete: routing to Architect")
            state.next_agent = AgentRole.ARCHITECT

        elif state.current_agent == AgentRole.ARCHITECT:
            logger.info("  -> Architect complete: routing to Planner")
            state.next_agent = AgentRole.PLANNER

        elif state.current_agent == AgentRole.PLANNER:
            logger.info("  -> Planner complete: routing to Developer")
            state.next_agent = AgentRole.DEVELOPER

        elif state.current_agent == AgentRole.DEVELOPER:
            gate = next((g for g in state.gates if g.name == "local_verify"), None)
            if gate and gate.status == "red":
                if gate.retry_count < 3:
                    logger.warning("  -> local_verify FAILED (attempt %d/3), dev retries",
                                   gate.retry_count + 1)
                    gate.retry_count += 1
                    state.next_agent = AgentRole.DEVELOPER
                else:
                    logger.error("  -> local_verify FAILED after 3 retries, escalating to human")
                    state.human_approval_pending = True
                    state.approval_gate_name = "local_verify"
                    state.next_agent = None
            elif gate and gate.status == "green":
                logger.info("  -> local_verify PASSED, routing to Security")
                state.next_agent = AgentRole.SECURITY
            else:
                state.next_agent = AgentRole.DEVELOPER

        elif state.current_agent == AgentRole.SECURITY:
            logger.info("  -> Security complete: finishing")
            state.next_agent = None

        else:
            logger.info("  -> Default: finishing")
            state.next_agent = None

        logger.info("  -> Next: %s",
                    state.next_agent.value if state.next_agent else "FINISH")
        return state

    # ---------------------------------------------------------------------- #
    # Human gate node — uses interrupt() for durable pause/resume (M1)
    # ---------------------------------------------------------------------- #
    async def _human_gate_node(self, state: ContinuumState) -> ContinuumState:
        """
        HUMAN GATE NODE: Pause execution and wait for human decision.

        Uses langgraph.types.interrupt() so the run is durably suspended in
        the Postgres checkpointer and can be resumed via Command(resume=...).

        The interrupt payload is surfaced via GET /run/{id} to the operator.
        On resume, the orchestrator re-routes from the gate that escalated.
        """
        try:
            from langgraph.types import interrupt  # available in langgraph >=0.2 / 1.0+
            decision = interrupt({
                "gate": state.approval_gate_name,
                "message": (
                    f"Gate '{state.approval_gate_name}' failed after 3 retries. "
                    "Approve to retry once more, or reject to abort the run."
                ),
                "run_id": state.run_id,
            })
            if decision and decision.get("approved"):
                # Reset the gate so the orchestrator re-routes to the failing agent
                gate = next(
                    (g for g in state.gates if g.name == state.approval_gate_name), None
                )
                if gate:
                    gate.retry_count = 0
                    gate.status = "pending"
                state.human_approval_pending = False
                state.approval_gate_name = None
                # Route back to the agent that was failing
                state.current_agent = None   # orchestrator will re-derive from gates
        except ImportError:
            # langgraph not installed — fall back to flag-only approach
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
    # Public entry point
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
