import React from 'react';
import type { AgentEvent } from '../types';

interface Props {
  events: AgentEvent[];
  onStageClick?: (role: string) => void;
}

// Canonical fallback order, used before any events arrive. The live offline
// pipeline emits a `developer` stage rather than the db/be/fe split; the spine
// adapts to whatever agents actually appear in the stream.
const FALLBACK_STAGES = ['bsa', 'architect', 'planner', 'developer', 'security', 'memory'];

const STAGE_LABELS: Record<string, string> = {
  bsa: 'BSA — story',
  architect: 'Architect — design',
  planner: 'Planner — DAG',
  database: 'Database',
  backend: 'Backend',
  frontend: 'Frontend',
  developer: 'Developer — code',
  security: 'Security — SAST',
  memory: 'Memory — episode',
};

type StageStatus = 'done' | 'running' | 'waiting' | 'blocked' | 'returned' | 'pending';

const GLYPH: Record<StageStatus, string> = {
  done: '✓',
  running: '⟳',
  waiting: '⏸',
  blocked: '▲',
  returned: '↩',
  pending: '○',
};

const COLOR: Record<StageStatus, string> = {
  done: 'text-emerald-400',
  running: 'text-sky-400 animate-pulse',
  waiting: 'text-amber-400',
  blocked: 'text-rose-400',
  returned: 'text-rose-400',
  pending: 'text-slate-600',
};

function orderedStages(events: AgentEvent[]): string[] {
  const seen: string[] = [];
  for (const ev of events) {
    if (ev.event_type === 'agent_start' && ev.agent && !seen.includes(ev.agent)) {
      seen.push(ev.agent);
    }
  }
  return seen.length > 0 ? seen : FALLBACK_STAGES;
}

function statusFor(role: string, events: AgentEvent[]): StageStatus {
  let status: StageStatus = 'pending';
  for (const ev of events) {
    if (ev.agent === role && ev.event_type === 'agent_start') status = 'running';
    if (ev.agent === role && ev.event_type === 'agent_complete') status = 'done';
    if (ev.agent === role && ev.event_type === 'run_blocked') status = 'blocked';
    if (
      ev.event_type === 'human_gate_pending' &&
      ev.agent === role &&
      status !== 'done'
    ) {
      status = 'waiting';
    }
  }
  // A later stage starting implies this one finished.
  const order = orderedStages(events);
  const idx = order.indexOf(role);
  if (status === 'running' && idx >= 0) {
    const laterStarted = order.slice(idx + 1).some((r) =>
      events.some((e) => e.agent === r && e.event_type === 'agent_start'),
    );
    if (laterStarted) status = 'done';
  }
  return status;
}

export function Spine({ events, onStageClick }: Props) {
  const stages = orderedStages(events);

  return (
    <div className="h-full overflow-y-auto p-6">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold mb-4">
        Pipeline Spine
      </p>
      <ol className="relative border-l border-[#2a3349] ml-3">
        {stages.map((role) => {
          const st = statusFor(role, events);
          return (
            <li key={role} className="mb-5 ml-5">
              <span
                className={`absolute -left-[9px] flex items-center justify-center w-4 h-4
                            rounded-full bg-[#0f1117] text-[11px] ${COLOR[st]}`}
              >
                {GLYPH[st]}
              </span>
              <button
                onClick={() => onStageClick?.(role)}
                className="text-left group"
              >
                <p
                  className={`text-xs font-medium ${
                    st === 'pending' ? 'text-slate-600' : 'text-slate-200'
                  } group-hover:text-sky-300 transition-colors`}
                >
                  {STAGE_LABELS[role] ?? role.toUpperCase()}
                </p>
                <p className={`text-[10px] ${COLOR[st]}`}>{st}</p>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
