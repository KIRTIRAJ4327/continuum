// ─── Agent event types emitted by the backend ────────────────────────────────
export type EventType =
  | 'agent_start'
  | 'agent_complete'
  | 'gate_green'
  | 'gate_red'
  | 'gate_retry'
  | 'human_gate_pending'
  | 'human_gate_resolved'
  | 'run_complete'
  | 'run_blocked'      // M6: gate stayed red past max retries
  | 'run_returned'     // M6: human rejected a story/design gate
  | 'pdlc_written'     // M8: .pdlc/ artifacts written to target repo
  // C2: observability cockpit — finer-grained events
  | 'tool_call'
  | 'llm_token'
  | 'agent_thinking'
  | 'agent_milestone'
  | 'artifact_ready'
  | 'sensor_result'
  | 'scope_checked'
  | 'evidence_built'
  | 'controlled_hold';

// C2: per-sensor result (ruff/mypy/pytest/bandit/openapi_contract)
export interface SensorResult {
  sensor: string;
  status: 'pass' | 'fail';
  detail: string;
}

// C2: a single tool invocation by an agent
export interface ToolCall {
  tool_name: string;
  args_preview: string;
  result_preview: string;
  duration_s: number;
}

// C2: a normalised step on the TraceTimeline (one pill per handoff/gate)
export interface TraceStep {
  seq: number;
  event_type: EventType;
  agent: string;
  timestamp: number;
  label: string;
}

export interface AgentEvent {
  event_type: EventType;
  agent: string;
  run_id: string;
  timestamp: number;
  seq?: number;   // C1: per-run monotonic sequence (SSE id / Last-Event-ID resume)
  data: Record<string, unknown>;
}

// ─── Run status (M6 canonical vocabulary) ────────────────────────────────────
export type RunStatus =
  | 'running'
  | 'waiting_gate'
  | 'blocked'
  | 'returned'
  | 'done'
  | 'failed';

// ─── M7: business mapping (code + label pair) ────────────────────────────────
export interface BusinessMapping {
  code: string;
  label: string;
}

export interface MappingFidelity {
  supplied: string[];
  found: string[];
  extra_in_code: string[];
  missing_in_code: string[];
  exact_match: boolean;
}

export interface RunSummary {
  run_id: string;
  request: string;
  status: RunStatus;
  // M6 metrics
  run_status?: RunStatus;
  cost_usd?: number;
  duration_s?: number | null;
  stage_idx?: number;
  stage_count?: number;
  reject_reason?: string | null;
  started_at: number | null;
  completed_at: number | null;
  current_agent: string | null;
  // M7 scope-guard
  business_mappings?: BusinessMapping[];
  mapping_fidelity?: MappingFidelity | null;
}

// ─── M6: one row of the 6-layer Evidence Stack ───────────────────────────────
export type EvidenceStatus = 'pass' | 'fail' | 'pending';

export interface EvidenceLayer {
  layer: string;
  sublabel?: string;
  status: EvidenceStatus;
  detail: string;
}

// ─── Agent node status derived from events ───────────────────────────────────
export type NodeStatus = 'idle' | 'running' | 'done' | 'failed' | 'waiting';

export interface AgentNodeState {
  role: string;
  label: string;
  status: NodeStatus;
  duration_s?: number;
}

// ─── Human gate waiting for approval ─────────────────────────────────────────
export interface PendingGate {
  gate_name: string;
  agent: string;
  run_id: string;
}
