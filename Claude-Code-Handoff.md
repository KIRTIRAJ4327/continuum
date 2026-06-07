# CLAUDE CODE HANDOFF BRIEF
## Continuum — M0 Skeleton (story → contract → code → PR)

---

## THE MISSION

You are building **Continuum**, an enterprise agentic SDLC pipeline. Your M0 job is to build the **skeleton** that takes a plain-English feature request, runs it through a supervised agent swarm (BSA → Architect → Planner → Frontend Dev), executes everything in a Hyper-V sandbox, and outputs a merge-ready PR.

**You are NOT building:**
- The UI (M2).
- The eval harness (M4).
- The Evolution Agent (M5).

**You ARE building:**
- A working LangGraph supervisor orchestrator that routes between agents.
- Agent skills that actually call LLMs, Neo4j, and Azure services.
- The sandbox client (Azure Container Apps dynamic sessions).
- The gates (lint/type/test/build).
- End-to-end integration so `make run` works.

---

## WHAT YOU GET

**Files already exist in the `continuum/` folder:**
- `Continuum-Agentic-SDLC-PRD.md` — the full specification (read this)
- `Continuum-Architecture.mermaid` — the system diagram
- `Continuum-Research-Report.md` — the research backing the design

**Starter stubs exist in these files:**
- `orchestrator/graph.py` — LangGraph skeleton
- `orchestrator/state.py` — state schema
- `agents/bsa.yaml`, `agents/architect.yaml` — agent specs
- `sandbox/aca_client.py` — ACA client stub
- `graph_db/driver.py` — Neo4j driver skeleton
- `harness/verify.sh` — local verification script
- `api/main.py` — FastAPI skeleton

All stubs have `# TODO:` comments. **These are yours to fill in.**

---

## THE STACK

| Component | Choice |
|---|---|
| **Language** | Python 3.10+ |
| **Orchestration** | LangGraph (supervisor pattern) |
| **Models** | Azure AI Foundry (`langchain-azure-ai`) |
| **Graph/State** | Neo4j (AuraDB or self-hosted) |
| **Sandbox** | Azure Container Apps dynamic sessions (Hyper-V) |
| **Checkpoints** | Azure DB for PostgreSQL (LangGraph Postgres checkpointer) |
| **Integrations** | Azure DevOps Remote MCP (work items, PRs, wiki) |
| **Web** | FastAPI (M2 will add React) |
| **Testing** | pytest, asyncio |

**Environment setup:** `pip install -r requirements.txt` (already written)

---

## THE M0 END-TO-END FLOW

```
User input: "Build a product dashboard"
    ↓
Orchestrator (LangGraph supervisor)
    ↓
→ BSA agent (create story in Jira/Boards)
    ↓ (reports back)
→ Orchestrator checks gate
    ↓
→ Architect agent (emit OpenAPI + DB schema + DAG)
    ↓ (reports back)
→ Orchestrator queries Neo4j: "what tasks are unblocked?"
    ↓
→ Planner agent (generate task checklist from DAG)
    ↓ (reports back)
→ Orchestrator routes to Frontend Dev
    ↓
→ Developer agent (write code, run in ACA sandbox)
    ↓ (code runs in sandbox)
→ Orchestrator checks gate: local_verify (lint+type+test+build)
    ↓
→ Gate green? → finish. Gate red + <3 retries? → dev fixes. >3 retries? → escalate to human.
    ↓
Output: merge-ready PR (tests pass, no lint errors, contract conforms)
```

---

## THE ORCHESTRATOR PATTERN (critical)

**Golden rule:** The orchestrator has **zero work tools**. It only:
1. Routes: `route(agent_name)` → pick the next agent
2. Finishes: `finish()` → end the run
3. Reads state from Neo4j and graph state
4. Never does spec work itself

**Structure:**
```python
@dataclass
class ContinuumState:
    request: str                  # Input
    current_agent: Optional[str]  # Which agent is running now
    gates: List[GateStatus]       # Gate results (green/red)
    story: Optional[Dict]         # Output from BSA
    contract: Optional[str]       # Output from Architect
    code: Optional[Dict]          # Output from Dev agents
    # ... other fields
```

**Graph shape:**
```
START → ORCHESTRATOR → [BSA | Architect | Planner | Developer | ... ] → ORCHESTRATOR → ... → FINISH
         (every agent reports back to orchestrator; orchestrator decides next step)
```

---

## KEY IMPLEMENTATION NOTES

### 1. Agent Loading & Specs (YAML)

Each agent has a YAML spec (`agents/bsa.yaml`, `agents/architect.yaml`, etc.):
```yaml
name: "BSA"
version: "1.0"
instructions: "Convert request to story..."
allowed_skills: [create_story, write_spec, graphrag_query]
model_tier: "strong"  # or "cheap"
```

**When the orchestrator routes to an agent:**
1. Load the YAML spec
2. Build a prompt from the instructions + current state
3. Call the model via `init_chat_model("azure_ai:...")`
4. Bind the agent's allowed skills as tools
5. Run the LLM once (or loop if it calls tools)
6. Update state with the result
7. Return to orchestrator

### 2. Skills as Atoms

A **skill** is a versioned, named function. E.g. `create_story@1.0`:
```python
async def create_story(request: str, jira_token: str) -> Dict[str, Any]:
    """Create a Jira story from the request."""
    # Call Jira REST or Azure DevOps MCP
    return {"story_id": "...", "story_url": "..."}
```

Skills are **NOT prompts**. They are real functions. They:
- Call external APIs (Jira, ADO, Neo4j, etc.)
- Run local tools (git, lint, tests)
- Have clear inputs/outputs
- Are versioned (`skills/create_story/v1.0/skill.py`)

### 3. Gates = Deterministic Sensors

A gate runs **no model**. It runs lint, type-check, tests, SAST, contract validation. E.g.:
```python
async def gate_local_verify(code_path: str) -> tuple[bool, str]:
    """Run lint, type, test, build. Return (pass: bool, output: str)."""
    lint_ok = subprocess.run(["ruff", "check", code_path]).returncode == 0
    type_ok = subprocess.run(["mypy", code_path]).returncode == 0
    test_ok = subprocess.run(["pytest", "tests/"]).returncode == 0
    
    passed = lint_ok and type_ok and test_ok
    output = f"lint={lint_ok}, type={type_ok}, test={test_ok}"
    return (passed, output)
```

Gates are **ground truth**. The orchestrator trusts them. If a gate is red, the agent re-runs (max 3x), then escalates.

### 4. Neo4j DAG

The Architect generates a DAG (tasks, dependencies). It goes into Neo4j:
```cypher
CREATE (t1:Task {id: "auth", status: "pending"})
CREATE (t2:Task {id: "dashboard", status: "pending"})
CREATE (t1)-[:DEPENDS_ON]->(t2)
```

The orchestrator queries: *"Give me all Task nodes with status=pending where all dependencies are done."* Those run in parallel.

### 5. Sandbox Execution

**All dev agent code runs in Azure Container Apps dynamic sessions.** This is non-negotiable.

```python
client = ACASessionClient(endpoint="...", token="...")
await client.create_session()
result = await client.execute_code(code_string)
# result = {"stdout": "...", "stderr": "...", "exit_code": 0}
```

No agent code touches the host. Ever.

### 6. The Local Mirror

`harness/verify.sh` runs the exact same lint/type/test as CI. Git-worktree isolation ensures parallel agents don't collide, but the sandbox is the security boundary.

---

## WHAT TO DO RIGHT NOW

### Phase 1 (foundation)
1. [ ] Flesh out `orchestrator/graph.py`:
   - Implement `_route()`: pick next agent based on state
   - Implement `_run_agent()`: load agent YAML, call LLM, run tools
   - Implement `_next_node()`: conditional edge logic
   - Wire the graph (add_node, add_edge, add_conditional_edges)

2. [ ] Implement `graph_db/driver.py`:
   - Connect to Neo4j
   - Implement `write_dag()`: create Task + DEPENDS_ON edges
   - Implement `get_unblocked_tasks()`: Cypher query

3. [ ] Implement `sandbox/aca_client.py`:
   - create_session()
   - execute_code()
   - cleanup()

### Phase 2 (skills & agents)
4. [ ] Implement 4 core skills:
   - `create_story`: call Azure DevOps MCP to create a story
   - `emit_contract`: call LLM, return OpenAPI YAML
   - `emit_schema`: call LLM, return SQL DDL
   - `write_code`: call LLM, return Python code

5. [ ] Implement the skeleton agents:
   - BSA: load `agents/bsa.yaml`, call LLM with `create_story` + `write_spec`
   - Architect: load `agents/architect.yaml`, call LLM with `emit_contract` + `emit_schema`
   - Planner: load `agents/planner.yaml`, query Neo4j DAG, generate checklist
   - Developer: load `agents/developer.yaml`, call LLM with `write_code`, run in sandbox

### Phase 3 (gates)
6. [ ] Implement gates in `orchestrator/gates.py`:
   - `local_verify`: ruff check, mypy, pytest (subprocess calls)
   - `contract_validate`: load generated OpenAPI, validate against schema
   - Wire gates into the graph: after each agent, check if green; if red, add to retry_counts

### Phase 4 (integration)
7. [ ] Implement `integrations/azure_devops_mcp.py`:
   - create_work_item()
   - create_pull_request()
   - get_pull_request_status()

8. [ ] Wire FastAPI in `api/main.py`:
   - POST `/run` — submit request, start orchestrator
   - GET `/run/{run_id}` — get current state

9. [ ] Write tests in `tests/`:
   - test_orchestrator_routes_to_bsa
   - test_gate_passes_on_clean_code
   - test_dag_written_to_neo4j

### Phase 5 (smoke test)
10. [ ] Make `make run` work:
    - Install deps
    - Start PostgreSQL + Neo4j locally (docker-compose? or assume they exist?)
    - Start FastAPI
    - POST a feature request
    - Watch it flow through BSA → Architect → Planner → Dev → gate

---

## CODE STYLE & CONVENTIONS

- **Type hints everywhere:** `async def foo(x: str) -> Dict[str, Any]:`
- **Docstrings:** Every function gets a docstring (what it does, args, returns)
- **Error handling:** Try-catch around external calls (LLM, Neo4j, ADO, sandbox)
- **Logging:** Use Python `logging` module; log every significant action (agent run, gate result, skill call)
- **Async:** Everything is `async`/`await`. No blocking calls in the orchestrator.
- **Environment variables:** Load from `.env` via `os.getenv()` or `python-dotenv`
- **Testing:** Pytest with fixtures; use `pytest-asyncio` for async tests

---

## RUNNING IT (locally)

```bash
# 1. Install
pip install -r requirements.txt

# 2. Set environment
cp .env.example .env
# Edit .env with real credentials

# 3. Verify local setup
make verify

# 4. Start services (Neo4j, PostgreSQL)
# ... (docker-compose or manual setup)

# 5. Start the orchestrator
make run

# 6. In another terminal, submit a request
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"request": "Build a user dashboard with recent activity"}'

# 7. Monitor the logs
# You should see:
#   → Orchestrator routes to BSA
#   → BSA creates story
#   → Orchestrator routes to Architect
#   → Architect emits contract + schema
#   → ...
#   → Gate green
#   → Done
```

---

## REFERENCES

- **PRD:** `Continuum-Agentic-SDLC-PRD.md` — read §4 (7 rules + PEV), §5 (architecture), §6 (agents), §8 (workflow)
- **Architecture:** `Continuum-Architecture.mermaid` — visualizes the planes and flow
- **LangGraph docs:** https://reference.langchain.com/python/langgraph
- **Azure DevOps MCP:** https://learn.microsoft.com/en-us/azure/devops/mcp-server/mcp-server-overview
- **Neo4j asyncio:** https://neo4j.com/docs/api/python-driver/current/api.html#async-driver

---

## KEY CONSTRAINTS & RULES (enforced in code)

1. **Orchestrator has no work tools.** It only routes and finishes.
2. **Max 3 retries per gate.** After 3 failures, escalate to human (set `human_approval_pending = True`).
3. **All code runs in sandbox.** Dev agents call `aca_client.execute_code()`, never subprocess on host.
4. **Gates are deterministic.** No model in the gate. Lint, type, test, build only.
5. **Skills are atoms.** Versioned, named, testable functions.
6. **Every agent reports back to orchestrator.** No direct agent-to-agent handoff.

---

## SUCCESS CRITERIA (M0 is done when...)

- [ ] `make run` starts the orchestrator
- [ ] Accepts a plain-English feature request
- [ ] Routes to BSA → Architect → Planner → Developer
- [ ] Developer code runs in ACA sandbox (not on host)
- [ ] Gate (local_verify) runs and passes
- [ ] All outputs (story, contract, schema, code) are generated
- [ ] Tests pass
- [ ] README + Makefile work
- [ ] Logs are clear and helpful

---

## QUESTIONS? 

Refer back to the PRD for design questions. Refer to the architecture diagram for flow questions. You've got this.

**Build M0. Hand it back when done. Then we iterate to M1 (real PRs, more agents), M2 (UI), etc.**

---

Good luck! 🚀
