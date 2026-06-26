import React, { useMemo, useState } from 'react';
import type { AgentEvent } from '../types';

interface Props {
  events: AgentEvent[];
}

// C2: a horizontal timeline of agent handoffs and gate decisions for a run.
// Each meaningful event becomes a pill in `seq` order. Agent starts/completes,
// gate decisions, human gates and run terminals are the load-bearing steps;
// tier-1 noise (tokens, thinking, tool calls) is filtered out so the timeline
// stays a readable spine of "what happened, in order".
const SHOWN = new Set([
  'agent_start',
  'agent_complete',
  'gate_green',
  'gate_red',
  'gate_retry',
  'human_gate_pending',
  'human_gate_resolved',
  'scope_checked',
  'evidence_built',
  'run_complete',
  'run_blocked',
  'run_returned',
]);

interface Pill {
  seq: number;
  glyph: string;
  cls: string;
  label: string;
  detail: string;
}

function toPill(ev: AgentEvent): Pill {
  const seq = ev.seq ?? 0;
  const agent = ev.agent ? ev.agent.toUpperCase() : '';
  switch (ev.event_type) {
    case 'agent_start':
      return { seq, glyph: '●', cls: 'bg-blue-500/20 text-blue-300 ring-blue-500/40', label: agent, detail: `${agent} started` };
    case 'agent_complete':
      return { seq, glyph: '✓', cls: 'bg-green-500/20 text-green-300 ring-green-500/40', label: agent, detail: `${agent} completed` };
    case 'gate_green':
      return { seq, glyph: '◆', cls: 'bg-green-500/20 text-green-300 ring-green-500/40', label: String(ev.data?.gate ?? 'gate'), detail: `gate ${ev.data?.gate ?? ''} passed` };
    case 'gate_red':
      return { seq, glyph: '◆', cls: 'bg-red-500/20 text-red-300 ring-red-500/40', label: String(ev.data?.gate ?? 'gate'), detail: `gate ${ev.data?.gate ?? ''} FAILED` };
    case 'gate_retry':
      return { seq, glyph: '↺', cls: 'bg-amber-500/20 text-amber-300 ring-amber-500/40', label: 'retry', detail: `retry ${ev.data?.gate_name ?? ''}` };
    case 'human_gate_pending':
      return { seq, glyph: '⏸', cls: 'bg-amber-500/20 text-amber-200 ring-amber-400/50', label: 'gate', detail: `human gate: ${ev.data?.gate_name ?? ''}` };
    case 'human_gate_resolved':
      return { seq, glyph: '▶', cls: 'bg-green-500/20 text-green-300 ring-green-500/40', label: 'resume', detail: `resolved: ${ev.data?.gate_name ?? ''}` };
    case 'scope_checked':
      return { seq, glyph: '⊡', cls: 'bg-teal-500/20 text-teal-300 ring-teal-500/40', label: 'scope', detail: ev.data?.exact_match ? 'scope exact match' : 'scope mismatch' };
    case 'evidence_built':
      return { seq, glyph: '▦', cls: 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/40', label: 'evidence', detail: 'evidence stack assembled' };
    case 'run_complete':
      return { seq, glyph: '■', cls: 'bg-slate-500/20 text-slate-300 ring-slate-500/40', label: 'done', detail: 'run complete' };
    case 'run_blocked':
      return { seq, glyph: '▲', cls: 'bg-rose-500/20 text-rose-300 ring-rose-500/40', label: 'blocked', detail: `blocked at ${ev.data?.gate_name ?? ''}` };
    case 'run_returned':
      return { seq, glyph: '↩', cls: 'bg-rose-500/20 text-rose-300 ring-rose-500/40', label: 'returned', detail: `returned at ${ev.data?.gate ?? ''}` };
    default:
      return { seq, glyph: '·', cls: 'bg-slate-700/40 text-slate-400 ring-slate-600/40', label: ev.event_type, detail: ev.event_type };
  }
}

export function TraceTimeline({ events }: Props) {
  const [active, setActive] = useState<number | null>(null);

  const pills = useMemo(() => {
    const seen = events.filter((e) => SHOWN.has(e.event_type));
    seen.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    return seen.map(toPill);
  }, [events]);

  // The last pill is the "active" node when the run is still live.
  const liveSeq = pills.length ? pills[pills.length - 1].seq : -1;

  if (pills.length === 0) {
    return (
      <div className="px-4 py-3 text-[11px] text-slate-600 border-b border-[#2a3349]">
        Trace timeline — agent handoffs and gate decisions appear here as the run progresses.
      </div>
    );
  }

  return (
    <div className="border-b border-[#2a3349] px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Trace</span>
        <span className="text-[10px] text-slate-600">{pills.length} steps</span>
      </div>
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {pills.map((p, i) => {
          const isLive = p.seq === liveSeq;
          return (
            <React.Fragment key={p.seq}>
              {i > 0 && <span className="text-slate-700 shrink-0">→</span>}
              <button
                onClick={() => setActive(active === p.seq ? null : p.seq)}
                title={p.detail}
                className={`shrink-0 flex items-center gap-1 px-2 py-1 rounded-full ring-1 text-[10px] font-mono transition-colors ${p.cls} ${isLive ? 'animate-pulse' : ''}`}
              >
                <span>{p.glyph}</span>
                <span className="max-w-[6rem] truncate">{p.label}</span>
              </button>
            </React.Fragment>
          );
        })}
      </div>
      {active !== null && (
        <div className="mt-2 text-[11px] text-slate-400 font-mono">
          {pills.find((p) => p.seq === active)?.detail}
        </div>
      )}
    </div>
  );
}
