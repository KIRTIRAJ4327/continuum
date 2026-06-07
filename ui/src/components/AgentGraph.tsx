import React, { useCallback, useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  type NodeProps,
  type Node,
  type Edge,
} from 'reactflow';
import 'reactflow/dist/style.css';
import type { AgentEvent, NodeStatus } from '../types';

// ─── Static pipeline definition ───────────────────────────────────────────────
const PIPELINE_NODES = [
  { id: 'bsa',       label: 'BSA',       x: 60,  y: 160 },
  { id: 'architect', label: 'Architect', x: 240, y: 160 },
  { id: 'planner',   label: 'Planner',   x: 420, y: 160 },
  { id: 'developer', label: 'Developer', x: 600, y: 160 },
  { id: 'security',  label: 'Security',  x: 780, y: 160 },
];

// Human gate nodes (displayed above the pipeline)
const GATE_NODES = [
  { id: 'gate_story',  label: 'Story\nReview',  x: 150, y: 40,  between: ['bsa', 'architect'] },
  { id: 'gate_design', label: 'Design\nReview', x: 330, y: 40,  between: ['architect', 'planner'] },
  { id: 'gate_merge',  label: 'Merge\nReview',  x: 690, y: 40,  between: ['developer', 'security'] },
];

// ─── Status → colors ──────────────────────────────────────────────────────────
const STATUS_BG: Record<NodeStatus, string> = {
  idle:    '#1e2535',
  running: '#1d3a6b',
  done:    '#14432a',
  failed:  '#450a0a',
  waiting: '#451f00',
};
const STATUS_BORDER: Record<NodeStatus, string> = {
  idle:    '#2a3349',
  running: '#3b82f6',
  done:    '#22c55e',
  failed:  '#ef4444',
  waiting: '#f59e0b',
};
const STATUS_DOT: Record<NodeStatus, string> = {
  idle:    'bg-slate-600',
  running: 'bg-blue-500 animate-pulse',
  done:    'bg-green-500',
  failed:  'bg-red-500',
  waiting: 'bg-amber-400 animate-pulse',
};

// ─── Custom agent node ────────────────────────────────────────────────────────
function AgentNode({ data }: NodeProps) {
  const { label, status, duration_s } = data as {
    label: string;
    status: NodeStatus;
    duration_s?: number;
  };
  return (
    <div
      style={{
        background: STATUS_BG[status],
        border: `2px solid ${STATUS_BORDER[status]}`,
        transition: 'all 0.3s ease',
      }}
      className="rounded-xl px-4 py-3 w-32 cursor-pointer select-none"
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[status]}`} />
        <span className="text-xs font-semibold text-slate-100 truncate">{label}</span>
      </div>
      {status === 'running' && (
        <p className="text-[10px] text-blue-300 mt-1">Running…</p>
      )}
      {status === 'done' && duration_s && (
        <p className="text-[10px] text-green-400 mt-1">{duration_s}s</p>
      )}
      {status === 'failed' && (
        <p className="text-[10px] text-red-300 mt-1">Failed</p>
      )}
      {status === 'waiting' && (
        <p className="text-[10px] text-amber-300 mt-1">Awaiting gate</p>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// ─── Custom gate node ─────────────────────────────────────────────────────────
function GateNode({ data }: NodeProps) {
  const { label, status } = data as { label: string; status: 'idle' | 'pending' | 'approved' | 'rejected' };
  const bg   = status === 'pending' ? '#451f00' : status === 'approved' ? '#14432a' : '#1e2535';
  const bord = status === 'pending' ? '#f59e0b' : status === 'approved' ? '#22c55e' : '#2a3349';
  return (
    <div
      style={{ background: bg, border: `2px solid ${bord}`, transition: 'all 0.3s' }}
      className="rounded-lg px-3 py-1.5 w-24 text-center"
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <p className="text-[10px] text-slate-300 whitespace-pre-line leading-tight">{label}</p>
      {status === 'pending' && (
        <span className="inline-block mt-1 text-[9px] bg-amber-500 text-black px-1 rounded">
          PENDING
        </span>
      )}
      {status === 'approved' && (
        <span className="inline-block mt-1 text-[9px] text-green-400">✓ Approved</span>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const NODE_TYPES = { agentNode: AgentNode, gateNode: GateNode };

// ─── Derive node/edge state from event log ────────────────────────────────────
function deriveAgentStatus(role: string, events: AgentEvent[]): { status: NodeStatus; duration_s?: number } {
  let status: NodeStatus = 'idle';
  let duration_s: number | undefined;
  for (const ev of events) {
    if (ev.agent !== role) continue;
    if (ev.event_type === 'agent_start') status = 'running';
    if (ev.event_type === 'agent_complete') {
      status = 'done';
      duration_s = (ev.data?.duration_s as number) ?? undefined;
    }
    if (ev.event_type === 'gate_red') status = 'failed';
    if (ev.event_type === 'human_gate_pending') status = 'waiting';
  }
  // Human gate resolved restores the agent to done
  for (const ev of events) {
    if (ev.event_type === 'human_gate_resolved') {
      const gate = ev.data?.gate_name as string;
      if (
        (gate === 'story_review'  && role === 'bsa') ||
        (gate === 'design_review' && role === 'architect') ||
        (gate === 'merge_review'  && role === 'security')
      ) {
        if (status === 'waiting') status = 'done';
      }
    }
  }
  return { status, duration_s };
}

function deriveGateStatus(gateId: string, events: AgentEvent[]): 'idle' | 'pending' | 'approved' | 'rejected' {
  const gateMap: Record<string, string> = {
    gate_story:  'story_review',
    gate_design: 'design_review',
    gate_merge:  'merge_review',
  };
  const gateName = gateMap[gateId];
  let s: 'idle' | 'pending' | 'approved' | 'rejected' = 'idle';
  for (const ev of events) {
    if (ev.event_type === 'human_gate_pending' && ev.data?.gate_name === gateName) {
      s = 'pending';
    }
    if (ev.event_type === 'human_gate_resolved' && ev.data?.gate_name === gateName) {
      s = (ev.data?.approved as boolean) ? 'approved' : 'rejected';
    }
  }
  return s;
}

// ─── Component ────────────────────────────────────────────────────────────────
interface Props {
  events: AgentEvent[];
  onNodeClick?: (role: string) => void;
}

export function AgentGraph({ events, onNodeClick }: Props) {
  const nodes: Node[] = useMemo(() => {
    const agentNodes: Node[] = PIPELINE_NODES.map((n) => {
      const { status, duration_s } = deriveAgentStatus(n.id, events);
      return {
        id:       n.id,
        type:     'agentNode',
        position: { x: n.x, y: n.y },
        data:     { label: n.label, status, duration_s },
      };
    });
    const gateNodes: Node[] = GATE_NODES.map((g) => ({
      id:       g.id,
      type:     'gateNode',
      position: { x: g.x, y: g.y },
      data:     { label: g.label, status: deriveGateStatus(g.id, events) },
    }));
    return [...agentNodes, ...gateNodes];
  }, [events]);

  const edges: Edge[] = useMemo(() => [
    // Pipeline backbone
    { id: 'e-bsa-story',      source: 'bsa',       target: 'gate_story',  animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-story-arch',     source: 'gate_story', target: 'architect',   animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-arch-design',    source: 'architect',  target: 'gate_design', animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-design-plan',    source: 'gate_design',target: 'planner',     animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-plan-dev',       source: 'planner',    target: 'developer',   animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-dev-merge',      source: 'developer',  target: 'gate_merge',  animated: false, style: { stroke: '#2a3349' } },
    { id: 'e-merge-sec',      source: 'gate_merge', target: 'security',    animated: false, style: { stroke: '#2a3349' } },
  ], []);

  // Animate edges that are "in flight" (the source node is running/done and target is still idle)
  const animatedEdges = useMemo(() => {
    const agentStatus = Object.fromEntries(
      PIPELINE_NODES.map((n) => [n.id, deriveAgentStatus(n.id, events).status])
    );
    return edges.map((e) => {
      const srcStatus = agentStatus[e.source] ?? 'idle';
      const shouldAnimate = srcStatus === 'running' || srcStatus === 'done';
      return {
        ...e,
        animated: shouldAnimate,
        style: {
          stroke: shouldAnimate ? '#3b82f6' : '#2a3349',
          strokeWidth: shouldAnimate ? 2 : 1,
        },
      };
    });
  }, [edges, events]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (onNodeClick && !node.id.startsWith('gate_')) {
        onNodeClick(node.id);
      }
    },
    [onNodeClick]
  );

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={animatedEdges}
        nodeTypes={NODE_TYPES}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        minZoom={0.5}
        maxZoom={2}
        attributionPosition="bottom-right"
      >
        <Background color="#2a3349" gap={24} size={1} />
        <Controls
          style={{ background: '#1e2535', border: '1px solid #2a3349', borderRadius: 8 }}
          showInteractive={false}
        />
        <MiniMap
          style={{ background: '#161b27', border: '1px solid #2a3349' }}
          nodeColor={(n) => STATUS_BORDER[(n.data?.status as NodeStatus) ?? 'idle']}
        />
      </ReactFlow>
    </div>
  );
}
