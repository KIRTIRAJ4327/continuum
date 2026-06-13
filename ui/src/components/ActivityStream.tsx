import React, { useEffect, useRef } from 'react';
import type { AgentEvent } from '../types';

interface Props {
  events: AgentEvent[];
  connected: boolean;
}

const EVENT_STYLES: Record<string, { icon: string; color: string }> = {
  agent_start:          { icon: '▶', color: 'text-blue-400'   },
  agent_complete:       { icon: '✓', color: 'text-green-400'  },
  gate_green:           { icon: '✓', color: 'text-green-400'  },
  gate_red:             { icon: '✗', color: 'text-red-400'    },
  gate_retry:           { icon: '↺', color: 'text-amber-400'  },
  human_gate_pending:   { icon: '⏸', color: 'text-amber-300'  },
  human_gate_resolved:  { icon: '▶', color: 'text-green-300'  },
  run_complete:         { icon: '■', color: 'text-slate-400'  },
  // M6
  run_blocked:          { icon: '▲', color: 'text-rose-400'   },
  run_returned:         { icon: '↩', color: 'text-rose-400'   },
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
      return `Run blocked — gate ${ev.data?.gate_name ?? ''} exhausted retries`;
    case 'run_returned':
      return `Run returned — ${ev.data?.reason ?? ''}`;
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
          const style = EVENT_STYLES[ev.event_type] ?? { icon: '·', color: 'text-slate-500' };
          return (
            <div
              key={i}
              className="flex items-start gap-2 px-2 py-1 rounded hover:bg-[#1e2535] transition-colors"
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
    </div>
  );
}
