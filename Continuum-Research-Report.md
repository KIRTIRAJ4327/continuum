# Pressure-Testing "Continuum": Five Sources, One Verdict
## Make Code the Harness, Sandbox the Code, and Gate on Evals

## TL;DR
- The five sources converge on a single thesis that **validates and sharpens** Continuum's bet: the pipeline (the *code* harness), not the LLM, is the product — and that harness is only trustworthy when agent-generated code runs in hardware-isolated microVM/Hyper-V sandboxes (Source 1), when its quality is proven by deterministic-sensor evals running in CI (Sources 4 & 5), and when its mostly-deterministic graph can spawn bounded dynamic sub-workflows for fan-out-heavy work (Source 2). The arXiv survey *"Code as Agent Harness"* (Source 3) is the unifying frame and should become Continuum's foundational citation.
- **Three changes are must-haves**: (1) add a microVM/Hyper-V sandbox tier (Azure Container Apps dynamic sessions now, Hyperlight-in-AKS later) as the *only* place agent-generated code executes — git-worktrees give correctness isolation, not security isolation; (2) build a production eval harness measuring `pass^k` (not `pass@k`), route accuracy, gate-pass rates, and trajectory quality on a golden dataset, gating every PR via the existing Azure DevOps MCP; (3) reframe the "7 rules" as an explicit Plan→Execute→Verify contract model with deterministic sensors, and make the harness itself a versioned, eval-governed artifact.
- **Two are high-value nice-to-haves**: bounded *dynamic task decomposition* for the Planner and codebase-wide audit agents (Source 2's pattern, fenced inside the contamination-firewall gates), and an "Evolution Agent" / observability loop (Source 3 §3.5) that proposes harness improvements offline against regression evals before any human promotes them.

---

## Key Findings

### Source 1 — "Harness-Driven Agents: Secure Podcast Pipeline in Hyperlight MicroVM Sandbox" (Microsoft Tech Community, Jun 2026)
A concrete two-plane pattern on the Microsoft Agent Framework. An **orchestration plane** runs on the host (the `WorkflowBuilder` graph, LLM clients, a deterministic `save_scripts` executor with no model in the loop). An **execution plane** runs inside a single **Hyperlight Wasm sandbox** — "the only place LLM-generated code is allowed to run." The single bridge between planes is one call: `call_tool("fetch_url", ...)`.

Four invariants form "the whole security argument": the model never sees the network; its only tool is `execute_code`; network access happens only when the guest itself runs `call_tool` from inside the sandbox; and the deterministic save step has no LLM when bytes hit disk. Framing: "The harness is the steering wheel — it does not pretend to be the seatbelt and the crumple zone. For that, it points you outward: run this somewhere isolated." When an agent runs `shutil.rmtree("/")`, "the agent deletes its own throwaway sandbox, the host never notices, and the next `execute_code` starts from a clean snapshot."

Hyperlight is Microsoft open-source (Apache 2.0, CNCF sandbox): hypervisor-backed microVMs with **no full guest OS**, sub-millisecond cold starts (~0.0009s execution reported), WASI-friendly capability boundaries. In the Agent Framework "Hyperlight CodeAct" integration, the CodeAct pattern (model writes one Python program, sandbox executes once, host tools via `call_tool`) "can cut latency roughly in half and token usage by more than 60%" on tool-heavy workloads. Important caveat: Hyperlight "isolates the model-generated code, not your tools" — host callbacks behind `call_tool` run with the host's credentials, so side-effecting ops must stay approval-gated direct tools.

For Azure-native production **today**, the battle-tested primitive is **Azure Container Apps dynamic sessions**: Hyper-V-isolated, pre-warmed, sub-second sandboxes via REST/MCP, billed on consumption ("US$0.03 per session-hour"). Microsoft: "Copilot uses over 400,000 dynamic sessions per day… Each user conversation uses its own Hyper-V isolated session." Python/Node/Shell/custom containers, egress controls, managed-identity auth, LangChain integration, Application Insights tracing prompt→agent→LLM→sandbox→response.

### Source 2 — Anthropic "Introducing dynamic workflows in Claude Code" (May 2026)
Dynamic workflows let Claude "dynamically write orchestration scripts that run tens to hundreds of parallel subagents in a single session, checking its work before anything reaches you." A dynamic workflow is **a JavaScript orchestration script Claude writes**; a background runtime executes it, coordinating subagents in parallel, keeping intermediate state in script variables, verifying results before they surface. The architectural inversion: "The orchestration logic — loops, branching, agent-count decisions, verification passes — lives in script variables, and the model's context window receives only the condensed results… The script holds the state; the model holds the judgment." Caps at 1,000 parallel subagents (`ultracode`), up to 16 concurrent.

Proof point: the Bun Zig→Rust port — "roughly 750,000 lines of Rust, 99.8% of the existing test suite passing, and eleven days from first commit to merge" — hundreds of parallel agents, two reviewers per file, adversarial agents; **"not yet in production."** Best uses: codebase-wide audits, large migrations, "critical work you need checked twice." Caveats: can consume substantially more tokens; a snag "might spend 5x more tokens recovering"; enterprise-disableable. Crucially, the committed-script orchestration is deterministic/re-runnable — "you write the control flow as plain code… only the work inside each `agent()` call is model-powered." That is the bridge to Continuum's deterministic graph.

### Source 3 — "Code as Agent Harness" (arXiv 2605.18747, Ning et al., May 2026)
The intellectual backbone. An **agent harness** = "the software layer that surrounds an LLM with tools, APIs, sandboxes, memory, validators, permission boundaries, execution loops, and feedback channels, thereby turning a stateless model into a functional agent." Thesis: "the bottleneck of autonomy is not only the reasoning ability of the base model, but also the reliability of the system that connects model outputs to long-horizon actions and persistent states."

Most actionable contributions:
- **Plan→Execute→Verify (PEV) loop (§3.4)** — harness as "cybernetic governor" turning intentions into "bounded, observable, and revisable state transitions." **Planning as Contract Formation (§3.4.2):** "The planning phase turns a user request into an explicit contract over the next state transition" — relevant files, expected invariants, validation commands, rollback points, risky ops, and which components may be read/edited. The plan is "a harness artifact rather than an unobserved reasoning trace."
- **Three-tier permission model (§3.4.3):** read-only; sandbox-edit (local patching/test execution in an isolated workspace); full-access (network, credentials, deployment, destructive ops) — tier-3 "should be guarded by mandatory human-in-the-loop gates." Substrates: coding sandboxes (Daytona, E2B), computer-use (OpenHands), durable runtimes (microVM/WASM, snapshots, warm pools).
- **Deterministic sensors (§3.4.4):** "linters, parsers, compilers, type checkers, unit tests, integration tests, static analyzers, fuzzers, runtime monitors, and CI pipelines," ordered by cost. "Termination should… be governed by verification rather than by model confidence."
- **Agentic Harness Engineering (§3.5):** deep telemetry as optimization substrate; an "Evolution Agent" running observe→diagnose→propose→evaluate→promote; **governed harness mutation** — "AHE should not be confused with unconstrained self-modification"; changes evaluated in sandboxes against fixed regression suites with auditable rationales; the Evolution Agent "is itself subject to the PEV loop."
- **Shared code-centric substrate (§4.3):** file-only / repository-based / execution-based / blackboard. "The Central Gap": most systems are file-only with no formal model of shared state — "the technical root of system brittleness." And: "topology complexity inversely correlates with harness-state formality."
- **Open challenges (§5.2):** evaluation beyond final task success; verification under incomplete feedback; regression-free harness improvement; transactional shared state; human oversight as harness state.

Empirical companion **Agentic Harness Engineering (arXiv 2604.25850, Lin et al.):** ten AHE iterations lifted pass@1 on Terminal-Bench 2 from 69.7% → 77.0%, beating hand-written Codex-CLI (71.9%); frozen harness transferred to SWE-bench-verified at ~12% fewer tokens and gave +5.1 to +10.1pp cross-model gains — harness quality is a first-class, model-independent performance lever.

### Sources 4 & 5 — Agent evaluation and the "evals to get trust" layer (2026)
(The referenced ID 2605.28773 resolves to an unrelated memory paper, "FluxMem"; the relevant 2026 eval literature is mapped below.)
- **`pass^k` over `pass@k`.** `pass@k` (≥1 success in k) measures capability and inflates with k; `pass^k` (all k succeed) measures reliability and falls with k. A 70%/trial agent reads ~97% on pass@3 but ~34% on pass^3. For autonomous operation, "`pass^k` is the true measure of production readiness." Princeton's HAL (Kapoor et al., ICLR 2026, arXiv:2510.11977): "the gap between capability (pass@k) and reliability (pass^k) is substantial across all models"; models "remain vulnerable to surface-level variations in how tasks are specified." HAL: "21,730 agent rollouts over nine agent models and nine benchmarks… ~$40,000"; increasing reasoning-token budget "lowered accuracy in 21 out of 36 tested settings."
- **The measurement loop:** golden dataset from real failures (20–50 seed cases; grow to 100+ CI + ≥500 production-trace cases), graders you trust, an LLM judge **calibrated against a human gold set** (target ≥0.80 Spearman; ~0.86 achievable), CI gate that blocks regressions. Scorer mix: ~60% deterministic (exact/regex/JSON-schema/latency), ~30% LLM-as-judge, ~10% human. **Block-on-regression, not block-on-absolute-threshold**; never set a bar without scoring a baseline first. (Gartner, Jun 2025: "Over 40% of agentic AI projects will be canceled by the end of 2027, due to escalating costs, unclear business value or inadequate risk controls.")
- **Trajectory/trace evaluation:** score the *path*, not just the answer — route accuracy, step efficiency, reasoning coherence — via typed spans. Tools: LangSmith (native LangGraph trajectory eval, OTel ingestion), Braintrust (GitHub Action posts per-scorer PR comments), DeepEval, Arize Phoenix, MLflow. **Agent-as-a-Judge** evaluates a full trajectory, not just outcomes.
- **Coding-agent benchmarks beyond SWE-bench:** **PRDBench** (50 project-level tasks from PRDs, 1,258 scoring points, Agent-as-a-Judge via fine-tuned PRDJudge >90% human alignment — directly analogous to Continuum's request→project flow); **FeatureBench** (feature-level via dependency-graph tracing; Claude 4.5 Opus 74.4% on SWE-bench but only 11.0% here); **c-CRAB / CR-Bench** (code-review-agent benchmarks converting human reviews into executable tests; agents solve only ~40%); **SaaSBench** (long-horizon enterprise SaaS); SlopCodeBench (long-horizon degradation). Lesson: "Build your own harness, on your own golden data, before citing any published leaderboard."

---

## How the five fit together
Continuum's "7 rules" are an informal prose version of what Source 3 formalizes. Source 3 gives vocabulary and rigor; Source 1 supplies the security substrate the survey calls for; Source 2 shows how to add bounded dynamism without abandoning determinism; Sources 4 & 5 supply the verification/trust layer the survey names as its #1 open challenge. In short: **code-as-harness is the theory, microVM sandboxing is the safety mechanism, evals are the proof, dynamic workflows are the scaling escape hatch.**

### Q1 — Should agent-generated code run in microVM/Hyperlight sandboxes? 
Yes — the single biggest gap today. Git-worktree isolation prevents agents from corrupting *each other's working trees*; it is a correctness/concurrency boundary, **not** a security boundary. Nothing in a worktree stops agent code from reading credentials, calling the network, or running `rm -rf` on the CI runner. Isolation hierarchy (weak→strong): namespaces → seccomp → gVisor → microVM (hardware/KVM boundary) → Wasm. Adopt a **two-plane model like Source 1**: keep LangGraph orchestration on the host; execute *all* agent-generated code inside a Hyper-V/microVM sandbox. Azure path: **start with Azure Container Apps dynamic sessions** (GA, Hyper-V isolated, MCP-accessible, managed identity, egress controls, App Insights), **evaluate Hyperlight-on-AKS** (Kata MicroVMs, or `agent-framework-hyperlight`, alpha) for the low-latency CodeAct path. Keep side-effecting tools (ADO MCP writes, deploys, credentialed calls) host-side and approval-gated.

### Q2 — Should the fixed supervisor graph become partially dynamic? 
Partially, carefully. The contamination-firewall gate model (anchoring to OpenAPI contracts, tests, SAST) is the crown jewel and must remain the deterministic spine. But Source 2 + the Bun port prove dynamic fan-out is right for breadth-heavy, verifiable subtasks: Planner decomposing into N parallel items, codebase-wide audits, large migrations. Synthesis: **let an agent dynamically generate the orchestration script/DAG, but (a) commit it as a versioned artifact, (b) make orchestration logic deterministic/re-runnable with only leaf `agent()` work model-powered, (c) route every spawned subagent's output back through the same exogenous gates, (d) bound fan-out and token budgets.** Treat dynamic decomposition as a *node type within* the supervisor graph, not a replacement.

### Q3 — What does a production-grade eval harness look like? 
A first-class subsystem, not a dashboard:
- **Golden dataset:** 20–50 seed plain-English feature requests from real failures → 100+ CI + ≥500 from production traces. Store cases/labels in Neo4j (linked to episodic memory) and/or Azure AI Search.
- **Metrics:** stage-level **gate-pass rate**, **route accuracy**, **`pass^k`** for end-to-end runs (k≥5), auto-fix-loop convergence (within max-3), trajectory/tool-selection efficiency, cost/latency per stage, security-gate false-negative rate.
- **Scorers:** ~60% deterministic (your exogenous gates *are* the deterministic scorers — OpenAPI conformance, test pass, SAST clean, JSON-schema), ~30% LLM/Agent-as-judge for BSA specs/architecture/PR-review quality (calibrated ≥0.80 Spearman vs human), ~10% human queue. Borrow PRDBench's rubric/PRDJudge and c-CRAB's "human-reviews-to-executable-tests."
- **CI:** run on every PR touching agent code/prompts/skills/topology, inside the sandbox tier, surfaced via Azure DevOps MCP as PR status checks. **Block on regression vs. committed baseline**, alert on borderline (~2pp). This operationalizes rule #3 for the agents themselves.

### Q4 — How does "code as harness" deepen the 7 rules?

| 7-rule | Source-3 refinement |
|---|---|
| Never push and pray | = PEV loop; termination "governed by verification rather than model confidence" |
| Every stage is a gate | = deterministic sensors (lint/type/test/SAST/contract) as the control signal |
| Verify locally what CI verifies remotely | = sandbox-edit permission tier runs the same sensors pre-merge |
| Bound the auto-fix loop | = verification-driven termination + bounded repair |
| Skills are atoms not prompts | = typed tool schemas/adapters; "code for acting"; CodeAct collapses tool round-trips |
| Production deploys are retags not rebuilds | = immutable, snapshot-restorable sandboxes; promote artifacts, not rebuilds |
| The pipeline is the product not the LLM | = the survey's entire thesis; AHE evidence (+7.3pp from harness alone) proves it |

Two rules to **add**: **(8) Plans are contracts** (every BSA/Architect/Planner output is an explicit, machine-checked contract — files touchable, invariants, validation commands, rollback points, risk tier); **(9) The harness is a versioned, eval-governed artifact** (harness changes flow through the Evolution-Agent loop and must pass regression evals before a human promotes them; tier-3 changes require HITL).

### Q5 — Practices Continuum is currently missing
- **Security:** hardware-isolated execution of agent code; formal three-tier permission model with tier-3 HITL; the principle that the harness isolates *model code* but credentialed host tools stay approval-gated.
- **Observability:** deep telemetry — structured traces linking model decision → harness action → environment state → outcome, with token cost, latency, tool args, permission requests, edited files, sandbox snapshots, test results, branch decisions, *rejected alternatives*, human interventions. Pipe to App Insights + an eval store; replayability across harness versions enables both debugging and the Evolution Agent.
- **Reliability:** `pass^k` measurement and the capability-vs-reliability gap; calibrated judges; regression-gated CI.
- **Cost:** token-budget caps per dynamic workflow (5x-recovery risk); CodeAct to cut tool round-trips (~50% latency, ~60% tokens); model tiering already present is endorsed.
- **Shared state:** move from implicit/file-only toward a formal substrate — the Neo4j DAG + episodic memory is well-positioned to become the "repository/blackboard" substrate the survey says most systems lack; add transactional semantics and explicit ground-truth-state vs. agent-belief-state tracking for parallel dev agents.

---

## Recommendations (prioritized)

**Must-have (P0) — adopt now**
1. **Add a microVM/Hyper-V sandbox execution tier.** All agent-generated/executed code runs only inside Azure Container Apps dynamic sessions (now) with egress allow-lists + managed identity; pilot Hyperlight CodeAct on AKS for the low-latency path. Keep git-worktrees for parallel correctness, but document they are *not* the security boundary. *Change course if:* dynamic-session cold-start/cost (US$0.03/session-hr × volume) bottlenecks → pre-warmed Hyperlight pool. *Why:* Source 1; the `rm -rf` failure mode is unmitigated today.
2. **Stand up a production eval harness as a first-class subsystem.** Golden dataset in Neo4j/Azure AI Search; `pass^k`, route-accuracy, gate-pass metrics; 60/30/10 scorer mix with a human-calibrated judge (≥0.80 Spearman); block-on-regression CI surfaced through the Azure DevOps MCP. *Threshold:* no agent/prompt/topology change merges if it regresses baseline; promote new baseline only after a green run at k≥5. *Why:* Sources 4 & 5; the trust layer and the survey's #1 open problem.
3. **Formalize the 7 rules into a PEV contract model with deterministic sensors and a 3-tier permission system.** Every BSA/Architect/Planner output an explicit machine-checked contract; termination verification-governed; tier-3 actions (deploy, credentials, network, destructive) behind HITL via `interrupt()`. *Why:* Source 3 §3.4.

**High-value nice-to-have (P1) — plan next**
4. **Bounded dynamic task decomposition as a graph node type.** Planner (+ audit/migration agents) generates a committed, deterministic orchestration script fanning out to parallel subagents, each re-entering the exogenous gates; cap fan-out + token budget. *Expand only once* eval `pass^k` on decomposition cases matches/beats the static path at acceptable cost. *Why:* Source 2 + Source 3 orchestration-based planning.
5. **Offline Evolution-Agent / observability loop.** Deep telemetry to App Insights + eval store; a meta-agent proposes harness edits evaluated against held-out regression evals; humans promote only non-regressing, auditable changes. *Why:* Source 3 §3.5 + arXiv 2604.25850's +7.3pp.
6. **Upgrade the shared-state substrate.** Neo4j DAG + episodic memory as a formal repository/blackboard substrate with ground-truth-vs-belief tracking and transactional semantics for parallel dev agents. *Why:* Source 3 §4.3 "Central Gap."
7. **Adopt benchmark techniques wholesale.** PRDBench-style rubric + Agent-as-a-Judge for the plain-English→project judge; c-CRAB/CR-Bench "human-reviews-to-executable-tests" for Code/PR Review; FeatureBench/SaaSBench long-horizon feature tasks in the golden set. *Why:* Sources 4 & 5.

---

## Caveats
- **Source 2** is a **research preview** and a vendor (Claude Code) feature; the Bun port is "not yet in production," token cost can be 5x. Adopt the *pattern* (committed deterministic orchestration + leaf model work + adversarial verification), not a product dependency. Continuum runs on Azure AI Foundry; Claude is available via Foundry, but the dynamic-workflow runtime is Claude-Code-specific.
- **Source 3** is a **survey/position paper**; §4.3 and parts of §3.5 are the authors' forward-looking position; several cited systems are unverified. Empirical companion (2604.25850) is promising but single-team (GPT-5.4/5.5, Terminal-Bench 2 / SWE-bench-verified).
- **The arXiv IDs were approximate:** 2605.18747 is correctly "Code as Agent Harness"; **2605.28773 is an unrelated memory paper (FluxMem), not an evals paper** — substituted the actual 2026 eval literature (PRDBench, FeatureBench, c-CRAB, CR-Bench, SaaSBench, τ-bench/`pass^k`). Reconfirm the ID if you had a specific eval paper in mind.
- **Hyperlight** is alpha (Linux/Windows, Python guest; .NET "coming soon"), CNCF-sandbox stage; **Azure Container Apps dynamic sessions** are GA and the safer near-term choice. MicroVMs add ~5MB+ memory/instance and modest cost — justified for untrusted code.
- **Eval vendor figures** (Spearman 0.80/0.86, ≥500-case thresholds, 60/30/10 mix) are informed practitioner guidance (Galileo, Digital Applied, Braintrust, Confident AI), not peer-reviewed. The "40% canceled by 2027" figure is **Gartner** (Jun 25 2025). The `pass^k`/`pass@k` math and the capability-reliability gap are well-supported (τ-bench; Princeton HAL, arXiv:2510.11977).

---

## Source list
1. Microsoft Tech Community — *Harness-Driven Agents: Secure Podcast Pipeline in Hyperlight MicroVM Sandbox* — https://techcommunity.microsoft.com/blog/azuredevcommunityblog/harness-driven-agents-secure-podcast-pipeline-in-hyperlight-microvm-sandbox/4525512
2. Microsoft Learn — *Hyperlight CodeAct* — https://learn.microsoft.com/en-us/agent-framework/integrations/hyperlight ; Agent Framework blog — *CodeAct with Hyperlight* — https://devblogs.microsoft.com/agent-framework/codeact-with-hyperlight/
3. Azure Container Apps dynamic sessions (GA) — https://techcommunity.microsoft.com/blog/appsonazureblog/azure-container-apps-dynamic-sessions-general-availability-and-more/4303561
4. Anthropic — *Introducing dynamic workflows in Claude Code* — https://claude.com/blog/introducing-dynamic-workflows-in-claude-code
5. arXiv 2605.18747 — *Code as Agent Harness* (Ning et al.) — https://arxiv.org/abs/2605.18747
6. arXiv 2604.25850 — *Agentic Harness Engineering* (Lin et al.)
7. Princeton HAL Reliability Dashboard / *Holistic Agent Leaderboard* (arXiv:2510.11977) — https://hal.cs.princeton.edu/reliability/
8. Digital Applied — *AI Agent Eval Frameworks 2026* — https://www.digitalapplied.com/blog/ai-agent-eval-frameworks-testing-guide-2026
9. Gartner — *Over 40% of Agentic AI Projects Will Be Canceled by End of 2027* (Jun 25 2025) — https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027
