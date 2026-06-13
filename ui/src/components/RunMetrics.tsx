import React from 'react';
import type { RunSummary } from '../types';

interface Props {
  run: RunSummary;
}

function fmtDuration(s: number | null): string {
  if (s === null) return '—';
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function fmtCost(usd: number): string {
  if (usd === 0) return '$0.00';
  return `$${usd.toFixed(2)}`;
}

export function RunMetrics({ run }: Props) {
  const st = run.run_status || run.status;
  if (st !== 'done' && st !== 'complete') return null;

  const gates = (run as any).gates as Array<{ retry_count: number }> | undefined;
  const firstPass = gates ? gates.every((g) => g.retry_count === 0) : true;

  return (
    <div className="px-3 py-3 border-t border-[#2a3349]">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold mb-2">
        Run Metrics
      </p>
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-[#161b27] border border-[#2a3349] rounded-lg p-2 text-center">
          <p className="text-[10px] text-slate-500">First-pass</p>
          <p className={`text-sm font-semibold ${firstPass ? 'text-emerald-400' : 'text-amber-400'}`}>
            {firstPass ? '✓' : '↺'}
          </p>
        </div>
        <div className="bg-[#161b27] border border-[#2a3349] rounded-lg p-2 text-center">
          <p className="text-[10px] text-slate-500">Lead time</p>
          <p className="text-xs font-semibold text-slate-200">{fmtDuration(run.duration_s)}</p>
        </div>
        <div className="bg-[#161b27] border border-[#2a3349] rounded-lg p-2 text-center">
          <p className="text-[10px] text-slate-500">Cost</p>
          <p className="text-xs font-semibold text-slate-200">{fmtCost(run.cost_usd)}</p>
        </div>
      </div>
    </div>
  );
}
