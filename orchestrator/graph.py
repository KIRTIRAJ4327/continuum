# File: continuum/orchestrator/graph.py
# UPDATED: Gate retry logic fully wired (Blocker 2 fix)

"""
LangGraph supervisor orchestrator for Continuum.
Handles routing, gating, retry logic, and state management.
"""
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresCheckpointer
import asyncio
import logging
from typing import Callable, Optional
from .state import ContinuumState, AgentRole, GateStatus
from .agent_runner import AgentContext, run_agent

logger = logging.getLogger(__name__)

class ContinuumGraph:
    """LangGraph supervisor orchestrator."""
    
    def __init__(self, checkpointer_uri: str):
        """
        Initialize the orchestrator graph.
        
        Args:
            checkpointer_uri: PostgreSQL connection string for durable checkpoints
        """
        self.checkpointer = PostgresCheckpointer(conn_string=checkpointer_uri)
        self.context = AgentContext.from_env()
        self.graph = self._build_graph()
    
    def _build_graph(self):
        """Build the LangGraph supervisor graph."""
        graph = StateGraph(ContinuumState)
        
        # Nodes
        graph.add_node("orchestrator", self._route)
        # Each agent node is a closure bound to its role, so the runner knows
        # which agent it is (LangGraph nodes only receive `state`).
        for role in ["bsa", "architect", "planner", "developer", "security",
                     "code_review", "pr_review", "test"]:
            graph.add_node(role, self._make_runner(role))
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
                "finish": "finish",
                "wait_human": "orchestrator",  # Re-enter after human approval
            }
        )
        
        # All agent nodes route back to orchestrator
        agent_nodes = ["bsa", "architect", "planner", "developer", "security", 
                      "code_review", "pr_review", "test"]
        for agent in agent_nodes:
            graph.add_edge(agent, "orchestrator")
        
        graph.add_edge("finish", END)
        
        return graph.compile(checkpointer=self.checkpointer)
    
    async def _route(self, state: ContinuumState) -> ContinuumState:
        """
        ORCHESTRATOR NODE: Decides which agent to run next based on current state and gates.
        
        Rules enforced here (from PRD §4):
        - Rule 2: Every stage is a gate (check gate status before routing)
        - Rule 4: Max 3 retries per gate (enforced by GateStatus.retry_count)
        - Rule 9: Harness is governed (no work tools; only routing)
        
        Logic:
        1. Check if there's a pending human approval → wait
        2. Check the last gate status
        3. If gate is red:
           - If retry_count < 3: re-route to the same agent to fix
           - If retry_count >= 3: escalate to human
        4. If gate is green: route to the next agent
        5. If no gates yet: start with BSA
        """
        logger.info(f"[ORCHESTRATOR] Routing decision for run {state.run_id}")
        
        # Rule: Never skip a gate; always check before routing (Rule 2)
        if state.human_approval_pending:
            logger.info(f"  → Human approval pending at gate '{state.approval_gate_name}', waiting...")
            state.next_agent = None  # Signal to _next_node to return "wait_human"
            return state
        
        # Determine next agent based on progress
        if state.current_agent is None:
            # First run: start with BSA
            logger.info("  → First run: routing to BSA")
            state.next_agent = AgentRole.BSA
        elif state.current_agent == AgentRole.BSA:
            # After BSA: route to Architect
            logger.info("  → BSA complete: routing to Architect")
            state.next_agent = AgentRole.ARCHITECT
        elif state.current_agent == AgentRole.ARCHITECT:
            # After Architect: route to Planner
            logger.info("  → Architect complete: routing to Planner")
            state.next_agent = AgentRole.PLANNER
        elif state.current_agent == AgentRole.PLANNER:
            # After Planner: route to Developer(s)
            # In M0: single developer. In M1+: parallel FE/BE/DB
            logger.info("  → Planner complete: routing to Developer")
            state.next_agent = AgentRole.DEVELOPER
        elif state.current_agent == AgentRole.DEVELOPER:
            # After dev: check local_verify gate
            gate = next((g for g in state.gates if g.name == "local_verify"), None)
            if gate:
                if gate.status == "red":
                    if gate.retry_count < 3:
                        # Rule 4: Max 3 retries per gate
                        logger.warning(f"  → local_verify FAILED (attempt {gate.retry_count + 1}/3), dev will fix")
                        gate.retry_count += 1
                        state.next_agent = AgentRole.DEVELOPER  # Same agent retries
                    else:
                        # Escalate to human
                        logger.error(f"  → local_verify FAILED after 3 retries, escalating to human")
                        state.human_approval_pending = True
                        state.approval_gate_name = "local_verify"
                        state.next_agent = None
                elif gate.status == "green":
                    # Gate passed: continue to Security
                    logger.info("  → local_verify PASSED, routing to Security")
                    state.next_agent = AgentRole.SECURITY
            else:
                # Gate hasn't run yet; run dev first, then gate will check
                state.next_agent = AgentRole.DEVELOPER
        elif state.current_agent == AgentRole.SECURITY:
            # After Security SAST: finish (M0) or Code Review (M1)
            # For M0: just finish. M1 will add code_review routing.
            logger.info("  → Security complete: finishing (M0)")
            state.next_agent = None  # Will route to finish in _next_node
        else:
            # Default: finish
            logger.info("  → Default: finishing")
            state.next_agent = None
        
        logger.info(f"  → Next agent: {state.next_agent.value if state.next_agent else 'FINISH'}")
        return state
    
    def _make_runner(self, agent: str) -> Callable:
        """Build a LangGraph node function bound to a specific agent role."""
        async def _node(state: ContinuumState) -> ContinuumState:
            return await self._run_agent(state, agent)
        _node.__name__ = f"run_{agent}"
        return _node

    async def _run_agent(self, state: ContinuumState, agent: str) -> ContinuumState:
        """
        RUN AGENT NODE: Execute a single agent.

        Delegates to `agent_runner.run_agent`, which:
        1. Loads the agent's YAML spec
        2. Builds a prompt from spec + current state
        3. Resolves the model and binds allowed skills as tools
        4. Runs the LLM tool-calling loop (or a deterministic offline path)
        5. Maps the produced artifacts onto ContinuumState

        Returns to the orchestrator with `state.current_agent` set.
        """
        return await run_agent(state, agent, self.context)
    
    async def _next_node(self, state: ContinuumState) -> str:
        """
        CONDITIONAL EDGE: Determine where to route after orchestrator decides.
        
        Returns the next node name (string) based on state.next_agent.
        """
        if state.human_approval_pending:
            return "wait_human"  # Re-enter orchestrator
        elif state.next_agent:
            return state.next_agent.value
        else:
            return "finish"
    
    async def _finish(self, state: ContinuumState) -> ContinuumState:
        """
        FINISH NODE: Summarize and wrap up the run.
        
        Outputs:
        - Run ID
        - Status (success / escalated / failed)
        - Generated artifacts (story, contract, code, PR URL)
        """
        state.completed_at = asyncio.get_event_loop().time()
        logger.info(f"[FINISH] Run {state.run_id} complete in {state.completed_at - state.started_at:.1f}s")
        return state
    
    async def run(self, request: str, thread_id: str) -> ContinuumState:
        """
        Execute the orchestrator graph for a feature request.
        
        Args:
            request: Plain-English feature request
            thread_id: Unique thread ID (for durable resumption)
        
        Returns:
            Final ContinuumState with all artifacts
        """
        import time
        initial_state = ContinuumState(
            request=request,
            run_id=thread_id,
            started_at=time.time()
        )
        logger.info(f"[START] Run {thread_id}: {request}")
        
        result = await self.graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}}
        )
        return result
