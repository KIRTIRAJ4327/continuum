import React from 'react';
import type { RunSummary, AgentEvent } from '../types';

interface Props {
  run?: RunSummary;
  events: AgentEvent[];
}

function fmtCost(c?: number): string {
  if (c === undefined || c === null) return '—';
  return `$${c.toFixed(2)}`;
}

function fmtDuration(s?: number | null): string {
  if (s === undefined || s === null) return '—';
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** First-pass / lead-time / model-cost summary for a completed run (M6). */
export function RunMetrics({ run, events }: Props) {
  // First-pass = no gate retried at all this run.
  const anyRetry = events.some((e) => e.event_type === 'gate_retry');
  const firstPass = !anyRetry;

  const metrics: Array<{ label: string; value: string; color: string }> = [
    {
      label: 'First-pass',
      value: firstPass ? 'Yes' : 'No',
      color: firstPass ? 'text-emerald-400' : 'text-amber-400',
    },
    { label: 'Lead time', value: fmtDuration(run?.duration_s), color: 'text-slate-200' },
    { label: 'Model cost', value: fmtCost(run?.cost_usd), color: 'text-slate-200' },
  ];

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
        Run Metrics
      </p>
      <div className="grid grid-cols-3 gap-2">
        {metrics.map((m) => (
          <div key={m.label} className="rounded-lg border border-[#2a3349] p-2 text-center">
            <p className={`text-sm font-semibold ${m.color}`}>{m.value}</p>
            <p className="text-[9px] text-slate-500 uppercase tracking-wide mt-0.5">{m.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
