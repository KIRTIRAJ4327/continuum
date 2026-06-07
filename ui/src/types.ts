// ─── Agent event types emitted by the backend ────────────────────────────────
export type EventType =
  | 'agent_start'
  | 'agent_complete'
  | 'gate_green'
  | 'gate_red'
  | 'gate_retry'
  | 'human_gate_pending'
  | 'human_gate_resolved'
  | 'run_complete';

export interface AgentEvent {
  event_type: EventType;
  agent: string;
  run_id: string;
  timestamp: number;
  data: Record<string, unknown>;
}

// ─── Run status ───────────────────────────────────────────────────────────────
export type RunStatus = 'running' | 'complete' | 'failed' | 'awaiting_approval';

export interface RunSummary {
  run_id: string;
  request: string;
  status: RunStatus;
  started_at: number | null;
  completed_at: number | null;
  current_agent: string | null;
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
