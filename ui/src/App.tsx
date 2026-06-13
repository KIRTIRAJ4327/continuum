import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { WorkQueue } from './components/WorkQueue';
import { AgentGraph } from './components/AgentGraph';
import { Spine } from './components/Spine';
import { ActivityStream } from './components/ActivityStream';
import { GateInbox } from './components/GateInbox';
import { ArtifactViewer } from './components/ArtifactViewer';
import { Blocked } from './components/Blocked';
import { Returned } from './components/Returned';
import { EvidenceStack } from './components/EvidenceStack';
import { RunMetrics } from './components/RunMetrics';
import { useSSE } from './hooks/useSSE';
import { listRuns } from './lib/api';
import type { RunSummary, PendingGate, RunStatus } from './types';

// Right panel tab modes
type RightTab = 'activity' | 'artifact' | 'evidence';
// Center view modes — Spine is primary nav; AgentGraph is the engineer view.
type CenterView = 'spine' | 'graph';

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>('activity');
  const [centerView, setCenterView] = useState<CenterView>('spine');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  // ── Load run list on mount and poll for updates ───────────────────────────
  useEffect(() => {
    async function load() {
      try {
        const r = await listRuns();
        setRuns(r);
        if (!activeRunId && r.length > 0) setActiveRunId(r[0].run_id);
      } catch { /* offline / not yet started */ }
    }
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [activeRunId]);

  // ── SSE for the active run ─────────────────────────────────────────────────
  const { events, connected, error: sseError } = useSSE(activeRunId);

  const activeRun = useMemo(
    () => runs.find((r) => r.run_id === activeRunId),
    [runs, activeRunId],
  );
  const runStatus: RunStatus | undefined = activeRun
    ? (activeRun.run_status ?? activeRun.status)
    : undefined;

  // ── Derive pending human gates from events ────────────────────────────────
  const pendingGates = useMemo<PendingGate[]>(() => {
    if (!activeRunId) return [];
    const pending: PendingGate[] = [];
    const resolved = new Set<string>();
    for (const ev of events) {
      if (ev.event_type === 'human_gate_resolved') {
        resolved.add(ev.data?.gate_name as string);
      }
    }
    for (const ev of events) {
      if (ev.event_type === 'human_gate_pending') {
        const gate = ev.data?.gate_name as string;
        if (!resolved.has(gate)) {
          pending.push({ gate_name: gate, agent: ev.agent, run_id: activeRunId });
        }
      }
    }
    return pending;
  }, [events, activeRunId]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleRunStarted = useCallback((runId: string) => {
    setActiveRunId(runId);
    setRightTab('activity');
    setCenterView('spine');
    setSelectedAgent(null);
    setRuns((prev) => [
      {
        run_id: runId,
        request: '…',
        status: 'running',
        run_status: 'running',
        started_at: Date.now() / 1000,
        completed_at: null,
        current_agent: null,
      },
      ...prev,
    ]);
  }, []);

  const handleStageClick = useCallback((role: string) => {
    setSelectedAgent(role);
    setRightTab('artifact');
  }, []);

  const handleGateResolved = useCallback((gateName: string, approved: boolean) => {
    console.log('Gate resolved', gateName, approved);
  }, []);

  const handleCloseArtifact = useCallback(() => {
    setSelectedAgent(null);
    setRightTab('activity');
  }, []);

  const isBlocked = runStatus === 'blocked';
  const isReturned = runStatus === 'returned';
  const isDone = runStatus === 'done';

  return (
    <div className="flex h-screen overflow-hidden bg-[#0f1117] text-white">
      {/* ── Sidebar: Work Queue ──────────────────────────────────────────── */}
      <aside className="w-64 shrink-0 border-r border-[#2a3349] flex flex-col overflow-hidden">
        <WorkQueue
          runs={runs}
          activeRunId={activeRunId}
          onSelect={setActiveRunId}
          onRunStarted={handleRunStarted}
        />
      </aside>

      {/* ── Main content ────────────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-[#2a3349] shrink-0">
          {activeRunId ? (
            <>
              <span className="text-[11px] font-mono text-slate-500">run/{activeRunId}</span>
              {runStatus && (
                <span className="text-[10px] text-slate-400 uppercase tracking-wide">
                  {runStatus.replace('_', ' ')}
                </span>
              )}
              {connected && (
                <span className="flex items-center gap-1 text-[10px] text-sky-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse" />
                  Live
                </span>
              )}
              {sseError && <span className="text-[10px] text-red-400">{sseError}</span>}

              {/* Center view toggle */}
              <div className="ml-auto flex gap-1 text-[10px]">
                {(['spine', 'graph'] as CenterView[]).map((v) => (
                  <button
                    key={v}
                    onClick={() => setCenterView(v)}
                    className={`px-2 py-1 rounded transition-colors ${
                      centerView === v
                        ? 'bg-[#1e2535] text-sky-300'
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                  >
                    {v === 'spine' ? 'Spine' : 'Engineer view'}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <span className="text-xs text-slate-600">Select or start a run</span>
          )}
        </header>

        {/* Center + right panel row */}
        <div className="flex flex-1 overflow-hidden">
          {/* ── Center: Spine or AgentGraph ──────────────────────────────── */}
          <div className="flex-1 overflow-hidden">
            {activeRunId ? (
              centerView === 'spine' ? (
                <Spine events={events} onStageClick={handleStageClick} />
              ) : (
                <AgentGraph events={events} onNodeClick={handleStageClick} />
              )
            ) : (
              <div className="flex h-full items-center justify-center">
                <div className="text-center space-y-3">
                  <div className="text-5xl">⟳</div>
                  <p className="text-slate-600 text-sm">
                    Submit a feature request to start the pipeline
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* ── Right panel ────────────────────────────────────────────── */}
          {activeRunId && (
            <div className="w-80 shrink-0 border-l border-[#2a3349] flex flex-col overflow-hidden">
              {isBlocked ? (
                <Blocked runId={activeRunId} events={events} />
              ) : isReturned ? (
                <Returned
                  events={events}
                  reason={activeRun?.reject_reason}
                  onBackToQueue={() => setActiveRunId(null)}
                />
              ) : (
                <>
                  <GateInbox pendingGates={pendingGates} onResolved={handleGateResolved} />

                  {/* Tab bar */}
                  <div className="flex border-b border-[#2a3349] shrink-0">
                    {(['activity', 'artifact', 'evidence'] as RightTab[]).map((tab) => (
                      <button
                        key={tab}
                        onClick={() => setRightTab(tab)}
                        className={`flex-1 py-2 text-xs font-medium capitalize transition-colors
                          ${rightTab === tab
                            ? 'text-sky-400 border-b-2 border-sky-500'
                            : 'text-slate-500 hover:text-slate-300'}`}
                      >
                        {tab === 'activity'
                          ? `Activity (${events.length})`
                          : tab === 'artifact'
                          ? `Artifact${selectedAgent ? `: ${selectedAgent}` : ''}`
                          : 'Evidence'}
                      </button>
                    ))}
                  </div>

                  {/* Tab content */}
                  <div className="flex-1 overflow-y-auto">
                    {rightTab === 'activity' && (
                      <ActivityStream events={events} connected={connected} />
                    )}
                    {rightTab === 'artifact' && (
                      <ArtifactViewer
                        runId={activeRunId}
                        agent={selectedAgent}
                        onClose={handleCloseArtifact}
                      />
                    )}
                    {rightTab === 'evidence' && (
                      <div className="p-3 space-y-4">
                        <RunMetrics run={activeRun} events={events} />
                        <EvidenceStack runId={activeRunId} />
                        {!isDone && (
                          <p className="text-[10px] text-slate-600 text-center">
                            Evidence finalises when the run completes.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
