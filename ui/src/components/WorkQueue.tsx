import React, { useState } from 'react';
import type { RunSummary, RunStatus } from '../types';
import { startRun } from '../lib/api';

interface Props {
  runs: RunSummary[];
  activeRunId: string | null;
  onSelect: (runId: string) => void;
  onRunStarted: (runId: string) => void;
}

// Status badge colours — reuses RunList's STATUS_COLORS convention, extended for
// the M6 vocabulary (running/waiting_gate/blocked/returned/done/failed).
const STATUS_COLORS: Record<string, string> = {
  running:      'bg-sky-500',
  waiting_gate: 'bg-amber-400',
  blocked:      'bg-rose-500',
  returned:     'bg-rose-400',
  done:         'bg-emerald-500',
  failed:       'bg-red-600',
};

const STATUS_LABELS: Record<string, string> = {
  running:      'Running',
  waiting_gate: 'Awaiting you',
  blocked:      'Blocked',
  returned:     'Returned',
  done:         'Done',
  failed:       'Failed',
};

function statusOf(run: RunSummary): RunStatus {
  return (run.run_status ?? run.status) as RunStatus;
}

function ago(ts: number | null): string {
  if (!ts) return '';
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function fmtCost(c?: number): string {
  if (c === undefined || c === null) return '';
  return `$${c.toFixed(2)}`;
}

function fmtDuration(s?: number | null): string {
  if (s === undefined || s === null) return '';
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function RunRow({
  run,
  active,
  onSelect,
}: {
  run: RunSummary;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const status = statusOf(run);
  return (
    <button
      onClick={() => onSelect(run.run_id)}
      className={`w-full text-left px-4 py-3 border-b border-[#2a3349] hover:bg-[#1e2535]
                  transition-colors ${active ? 'bg-[#1e2535]' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs text-slate-200 leading-snug line-clamp-2 flex-1">{run.request}</p>
        <span
          className={`shrink-0 mt-0.5 inline-block w-1.5 h-1.5 rounded-full ${STATUS_COLORS[status] ?? 'bg-slate-600'}`}
        />
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className="text-[10px] text-slate-500 font-mono">{run.run_id}</span>
        <span className="text-[10px] text-slate-600">·</span>
        <span className="text-[10px] text-slate-500">{STATUS_LABELS[status] ?? status}</span>
        <span className="text-[10px] text-slate-600 ml-auto">{ago(run.started_at)}</span>
      </div>
      {(run.cost_usd !== undefined || run.duration_s) && (
        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-600 font-mono">
          {run.cost_usd !== undefined && <span>{fmtCost(run.cost_usd)}</span>}
          {run.duration_s ? <span>{fmtDuration(run.duration_s)}</span> : null}
        </div>
      )}
    </button>
  );
}

export function WorkQueue({ runs, activeRunId, onSelect, onRunStarted }: Props) {
  const [input, setInput] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const req = input.trim();
    if (!req) return;
    setSubmitting(true);
    try {
      const { run_id } = await startRun(req);
      setInput('');
      onRunStarted(run_id);
    } catch (err) {
      console.error('Failed to start run', err);
    } finally {
      setSubmitting(false);
    }
  }

  const waiting = runs.filter((r) => {
    const s = statusOf(r);
    return s === 'waiting_gate' || s === 'blocked' || s === 'returned';
  });
  const others = runs.filter((r) => !waiting.includes(r));

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[#2a3349]">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full bg-sky-500" />
          <span className="text-xs font-semibold tracking-widest text-slate-400 uppercase">
            Continuum
          </span>
        </div>
        <p className="text-[10px] text-slate-600">Work Queue · Agentic SDLC</p>
      </div>

      {/* New run form */}
      <form onSubmit={handleSubmit} className="p-3 border-b border-[#2a3349]">
        <textarea
          className="w-full bg-[#1e2535] border border-[#2a3349] rounded-lg p-2 text-xs text-slate-200
                     placeholder-slate-600 resize-none focus:outline-none focus:border-sky-500
                     transition-colors"
          rows={3}
          placeholder="Describe a feature to build…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={submitting}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e);
          }}
        />
        <button
          type="submit"
          disabled={submitting || !input.trim()}
          className="mt-2 w-full py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500
                     disabled:opacity-40 disabled:cursor-not-allowed text-xs font-medium
                     transition-colors"
        >
          {submitting ? 'Starting…' : '▶  Run pipeline'}
        </button>
        <p className="mt-1 text-[10px] text-slate-600 text-center">⌘↵ to submit</p>
      </form>

      {/* Run list */}
      <div className="flex-1 overflow-y-auto">
        {runs.length === 0 && (
          <p className="text-xs text-slate-600 text-center mt-8 px-4">
            No runs yet. Submit a feature request above.
          </p>
        )}

        {waiting.length > 0 && (
          <>
            <p className="px-4 pt-3 pb-1 text-[10px] text-amber-400 uppercase tracking-widest font-semibold">
              Waiting on you ({waiting.length})
            </p>
            {waiting.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                active={activeRunId === run.run_id}
                onSelect={onSelect}
              />
            ))}
          </>
        )}

        {others.length > 0 && (
          <>
            <p className="px-4 pt-3 pb-1 text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
              All runs
            </p>
            {others.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                active={activeRunId === run.run_id}
                onSelect={onSelect}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
