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
  // M6 additions
  | 'run_blocked'
  | 'run_returned';

export interface AgentEvent {
  event_type: EventType;
  agent: string;
  run_id: string;
  timestamp: number;
  data: Record<string, unknown>;
}

// ─── Run status ───────────────────────────────────────────────────────────────
export type RunStatus =
  | 'running'
  | 'waiting_gate'
  | 'blocked'
  | 'returned'
  | 'done'
  | 'failed'
  // legacy values (backward compat with old stored runs)
  | 'complete'
  | 'awaiting_approval';

export interface RunSummary {
  run_id: string;
  request: string;
  status: RunStatus;
  run_status: RunStatus;
  started_at: number | null;
  completed_at: number | null;
  current_agent: string | null;
  // M6 additions
  cost_usd: number;
  duration_s: number | null;
  stage_idx: number;
  reject_reason: string | null;
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

// ─── Evidence stack (M6) ─────────────────────────────────────────────────────
export interface EvidenceLayer {
  layer: number;
  name: string;
  sub_label: string;
  status: 'pass' | 'fail' | 'pending';
  detail: string;
}
