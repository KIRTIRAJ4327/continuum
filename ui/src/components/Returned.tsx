import React from 'react';
import type { AgentEvent } from '../types';

interface Props {
  events: AgentEvent[];
  reason?: string | null;
  onBackToQueue?: () => void;
}

/** Right-panel view shown when a run's status is `returned` (human rejected a gate). */
export function Returned({ events, reason, onBackToQueue }: Props) {
  const returnedEv = [...events].reverse().find((e) => e.event_type === 'run_returned');
  const gate = (returnedEv?.data?.gate as string) ?? 'review gate';
  const why = reason || (returnedEv?.data?.reason as string) || 'No reason provided.';

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2a3349] shrink-0">
        <span className="text-rose-400 text-base">↩</span>
        <span className="text-xs font-semibold text-rose-300 uppercase tracking-wider">
          Returned — {gate}
        </span>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        <p className="text-xs text-slate-400 leading-relaxed">
          A reviewer returned this run at the <span className="font-mono text-rose-300">{gate}</span>{' '}
          gate. The author should address the feedback and resubmit a new run.
        </p>

        <div>
          <p className="text-[10px] text-slate-500 uppercase mb-1">Reason</p>
          <div className="bg-[#0f1117] border border-rose-500/30 rounded-lg p-3 text-xs text-rose-200/90">
            {why}
          </div>
        </div>

        {onBackToQueue && (
          <button
            onClick={onBackToQueue}
            className="w-full py-2 rounded-lg bg-[#1e2535] hover:bg-[#252d40]
                       text-xs font-semibold text-slate-300 transition-colors"
          >
            ←  Back to Work Queue
          </button>
        )}
      </div>
    </div>
  );
}
