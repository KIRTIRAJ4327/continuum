import React, { useEffect, useRef } from 'react';
import type { AgentEvent } from '../types';

interface Props {
  events: AgentEvent[];
  connected: boolean;
}

// `tier` drives the 3-tier log emphasis (C2):
//   1 = dim background reasoning (agent_thinking, tool_call, llm_token)
//   2 = normal activity (sensor_result, scope_checked, agent_start)
//   3 = highlighted milestone / decision (milestones, artifacts, gates, human)
const EVENT_STYLES: Record<string, { icon: string; color: string; tier: 1 | 2 | 3 }> = {
  agent_start:          { icon: '▶', color: 'text-blue-400',   tier: 2 },
  agent_complete:       { icon: '✓', color: 'text-green-400',  tier: 2 },
  gate_green:           { icon: '✓', color: 'text-green-400',  tier: 3 },
  gate_red:             { icon: '✗', color: 'text-red-400',    tier: 3 },
  gate_retry:           { icon: '↺', color: 'text-amber-400',  tier: 3 },
  human_gate_pending:   { icon: '⏸', color: 'text-amber-300',  tier: 3 },
  human_gate_resolved:  { icon: '▶', color: 'text-green-300',  tier: 3 },
  run_complete:         { icon: '■', color: 'text-slate-400',  tier: 3 },
  run_blocked:          { icon: '▲', color: 'text-rose-400',   tier: 3 },
  run_returned:         { icon: '↩', color: 'text-rose-400',   tier: 3 },
  pdlc_written:         { icon: '▣', color: 'text-violet-400', tier: 3 },
  // C2 events
  tool_call:            { icon: '⚙', color: 'text-slate-500',  tier: 1 },
  llm_token:            { icon: '·', color: 'text-slate-600',  tier: 1 },
  agent_thinking:       { icon: '…', color: 'text-slate-500',  tier: 1 },
  agent_milestone:      { icon: '◆', color: 'text-cyan-300',   tier: 3 },
  artifact_ready:       { icon: '▣', color: 'text-violet-300', tier: 2 },
  sensor_result:        { icon: '○', color: 'text-slate-300',  tier: 2 },
  scope_checked:        { icon: '⊡', color: 'text-teal-300',   tier: 2 },
  evidence_built:       { icon: '▦', color: 'text-emerald-300', tier: 3 },
  controlled_hold:      { icon: '⏸', color: 'text-rose-300',   tier: 3 },
};

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function eventSummary(ev: AgentEvent): string {
  switch (ev.event_type) {
    case 'agent_start':
      return `${ev.agent.toUpperCase()} started`;
    case 'agent_complete': {
      const dur = ev.data?.duration_s;
      return `${ev.agent.toUpperCase()} completed${dur ? ` in ${dur}s` : ''}`;
    }
    case 'gate_green':
      return `Gate ${ev.data?.gate ?? ''} passed`;
    case 'gate_red':
      return `Gate ${ev.data?.gate ?? ''} FAILED`;
    case 'gate_retry':
      return `Retrying ${ev.data?.gate_name ?? ''} (attempt ${ev.data?.attempt ?? ''})`;
    case 'human_gate_pending':
      return `Human gate pending: ${ev.data?.gate_name ?? ''} — waiting for approval`;
    case 'human_gate_resolved':
      return `Human gate resolved: ${ev.data?.gate_name ?? ''} — ${ev.data?.approved ? 'APPROVED' : 'REJECTED'}`;
    case 'run_complete':
      return `Run complete (${ev.data?.status ?? ''})`;
    case 'run_blocked':
      return `Run BLOCKED at ${ev.data?.gate_name ?? ''} — needs implementation fix`;
    case 'run_returned':
      return `Run RETURNED at ${ev.data?.gate ?? ''}${ev.data?.reason ? ` — ${ev.data.reason}` : ''}`;
    case 'pdlc_written':
      return `Layer 2 artifacts written (${ev.data?.files_written ?? 0} files → .pdlc/)`;
    // C2 events
    case 'tool_call':
      return `${ev.agent.toUpperCase()} called ${ev.data?.tool_name ?? 'tool'}${ev.data?.duration_s ? ` (${ev.data.duration_s}s)` : ''}`;
    case 'llm_token':
      return String(ev.data?.token ?? '');
    case 'agent_thinking':
      return String(ev.data?.thought ?? 'thinking…');
    case 'agent_milestone':
      return `${ev.agent.toUpperCase()}: ${ev.data?.message ?? ''}`;
    case 'artifact_ready':
      return `${ev.agent.toUpperCase()} produced ${ev.data?.artifact_type ?? 'artifact'}`;
    case 'sensor_result':
      return `${ev.data?.sensor ?? 'sensor'} → ${String(ev.data?.status ?? '').toUpperCase()}${ev.data?.detail ? ` — ${ev.data.detail}` : ''}`;
    case 'scope_checked': {
      const ok = ev.data?.exact_match;
      const extra = (ev.data?.extra_in_code as string[] | undefined) ?? [];
      const missing = (ev.data?.missing_in_code as string[] | undefined) ?? [];
      if (ok) return 'Scope conformance: exact match';
      const parts: string[] = [];
      if (missing.length) parts.push(`missing ${missing.join(', ')}`);
      if (extra.length) parts.push(`extra ${extra.join(', ')}`);
      return `Scope mismatch — ${parts.join('; ') || 'see mapping fidelity'}`;
    }
    case 'evidence_built': {
      const layers = (ev.data?.layers as Array<{ status: string }> | undefined) ?? [];
      const pass = layers.filter((l) => l.status === 'pass').length;
      return `Evidence stack assembled — ${pass}/${layers.length} layers pass`;
    }
    case 'controlled_hold':
      return `Run held at ${ev.data?.gate ?? ''} — ${ev.data?.reason ?? ''}`;
    default:
      return ev.event_type;
  }
}

export function ActivityStream({ events, connected }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as events arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2a3349] shrink-0">
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Activity
        </span>
        {connected && (
          <span className="ml-auto flex items-center gap-1 text-[10px] text-blue-400">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
            Live
          </span>
        )}
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5 font-mono text-xs">
        {events.length === 0 && (
          <p className="text-slate-600 text-center mt-8 text-[11px]">
            Events will appear here when the pipeline runs.
          </p>
        )}
        {events.map((ev, i) => {
          const style = EVENT_STYLES[ev.event_type] ?? { icon: '·', color: 'text-slate-500', tier: 2 as const };
          // C2: tier-1 reasoning is dim/smaller; tier-3 decisions are emphasised.
          const tierCls =
            style.tier === 1 ? 'opacity-60 text-[10px]'
            : style.tier === 3 ? 'font-semibold'
            : '';
          return (
            <div
              key={ev.seq ?? i}
              className={`flex items-start gap-2 px-2 py-1 rounded hover:bg-[#1e2535] transition-colors ${tierCls}`}
            >
              <span className={`${style.color} w-3 shrink-0 text-center`}>{style.icon}</span>
              <span className="text-slate-600 shrink-0 text-[10px] mt-px w-20">
                {fmtTime(ev.timestamp)}
              </span>
              <span className={`${style.color} flex-1 leading-snug`}>{eventSummary(ev)}</span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      {/* C2: reassurance row — what the agent is NOT permitted to do. Always
          visible so an operator sees the guard-rails while a run is live. */}
      {connected && (
        <div className="shrink-0 border-t border-[#2a3349] px-3 py-2 text-[10px] text-slate-500 space-y-0.5">
          <div>✗ Cannot merge without your approval</div>
          <div>✗ Cannot deploy without your approval</div>
          <div>✗ Cannot modify this pipeline&apos;s rules</div>
        </div>
      )}
    </div>
  );
}
