import React from 'react';
import type { AgentEvent, RunStatus } from '../types';

const STAGES = [
  { role: 'bsa',       label: 'BSA' },
  { role: 'architect', label: 'Architect' },
  { role: 'planner',   label: 'Planner' },
  { role: 'database',  label: 'Database' },
  { role: 'backend',   label: 'Backend' },
  { role: 'frontend',  label: 'Frontend' },
  { role: 'security',  label: 'Security' },
  { role: 'memory',    label: 'Memory' },
];

type StageState = 'pending' | 'running' | 'done' | 'waiting' | 'blocked' | 'returned';

interface Props {
  events: AgentEvent[];
  runStatus: RunStatus;
  onNodeClick?: (role: string) => void;
  onToggleEngineerView?: () => void;
}

function deriveStageState(
  role: string,
  events: AgentEvent[],
  runStatus: RunStatus,
): StageState {
  let state: StageState = 'pending';
  for (const ev of events) {
    if (ev.agent !== role) continue;
    if (ev.event_type === 'agent_start') state = 'running';
    if (ev.event_type === 'agent_complete') state = 'done';
    if (ev.event_type === 'gate_red') state = 'blocked';
    if (ev.event_type === 'human_gate_pending') state = 'waiting';
  }
  // If run is returned/blocked, the last active stage inherits that status
  if (state === 'running' && runStatus === 'blocked') return 'blocked';
  if (state === 'running' && runStatus === 'returned') return 'returned';
  return state;
}

const STAGE_ICONS: Record<StageState, string> = {
  pending:  '○',
  running:  '⟳',
  done:     '✓',
  waiting:  '⏸',
  blocked:  '▲',
  returned: '↩',
};

const STAGE_COLORS: Record<StageState, { text: string; border: string; bg: string }> = {
  pending:  { text: 'text-slate-500',   border: 'border-[#2a3349]',      bg: 'bg-[#1e2535]' },
  running:  { text: 'text-sky-400',     border: 'border-sky-500',        bg: 'bg-[#0c2a4a]' },
  done:     { text: 'text-emerald-400', border: 'border-emerald-600',    bg: 'bg-[#0d3320]' },
  waiting:  { text: 'text-amber-400',   border: 'border-amber-500',      bg: 'bg-[#2a1f00]' },
  blocked:  { text: 'text-rose-400',    border: 'border-rose-600',       bg: 'bg-[#2a0d0d]' },
  returned: { text: 'text-rose-400',    border: 'border-rose-500/60',    bg: 'bg-[#2a0d0d]' },
};

export function Spine({ events, runStatus, onNodeClick, onToggleEngineerView }: Props) {
  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-5 pt-4 pb-2 shrink-0">
        <span className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
          Pipeline Stages
        </span>
        {onToggleEngineerView && (
          <button
            onClick={onToggleEngineerView}
            className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            ⬡ graph view
          </button>
        )}
      </div>

      {/* Stage list */}
      <div className="flex-1 px-4 pb-4 space-y-1.5">
        {STAGES.map(({ role, label }, idx) => {
          const stageState = deriveStageState(role, events, runStatus);
          const c = STAGE_COLORS[stageState];
          const isRunning = stageState === 'running';

          return (
            <button
              key={role}
              onClick={() => onNodeClick?.(role)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border
                          text-left transition-colors hover:bg-[#252d40] ${c.bg} ${c.border}`}
            >
              {/* Step number */}
              <span className="text-[10px] text-slate-600 w-4 shrink-0 text-right">
                {idx + 1}
              </span>

              {/* Icon */}
              <span
                className={`text-sm w-4 shrink-0 text-center ${c.text} ${isRunning ? 'animate-spin' : ''}`}
                style={isRunning ? { animationDuration: '2s' } : undefined}
              >
                {STAGE_ICONS[stageState]}
              </span>

              {/* Label */}
              <span className={`text-xs font-medium flex-1 ${c.text}`}>{label}</span>

              {/* State label */}
              <span className={`text-[10px] ${c.text} uppercase tracking-wide`}>
                {stageState !== 'pending' ? stageState : ''}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
