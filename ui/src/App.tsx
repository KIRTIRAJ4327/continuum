import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { WorkQueue } from './components/WorkQueue';
import { Spine } from './components/Spine';
import { AgentGraph } from './components/AgentGraph';
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

type RightTab = 'activity' | 'artifact' | 'evidence';

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>('activity');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [showEngineerView, setShowEngineerView] = useState(false);

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

  // ── Derive pending human gates from events ────────────────────────────────
  const pendingGates = useMemo<PendingGate[]>(() => {
    if (!activeRunId) return [];
    const resolved = new Set<string>();
    for (const ev of events) {
      if (ev.event_type === 'human_gate_resolved') {
        resolved.add(ev.data?.gate_name as string);
      }
    }
    const pending: PendingGate[] = [];
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

  // ── Active run details ─────────────────────────────────────────────────────
  const activeRun = useMemo<RunSummary | null>(
    () => runs.find((r) => r.run_id === activeRunId) ?? null,
    [runs, activeRunId],
  );
  const runStatus: RunStatus = (activeRun?.run_status || activeRun?.status || 'running') as RunStatus;

  // ── Derive blocked gate info from events ─────────────────────────────────
  const blockedInfo = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === 'run_blocked') {
        return {
          gateName: events[i].data?.gate_name as string | undefined,
          errorMessage: events[i].data?.error_message as string | undefined,
        };
      }
    }
    return null;
  }, [events]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleRunStarted = useCallback((runId: string) => {
    setActiveRunId(runId);
    setRightTab('activity');
    setSelectedAgent(null);
    setShowEngineerView(false);
    setRuns((prev) => [
      {
        run_id: runId,
        request: '…',
        status: 'running',
        run_status: 'running',
        started_at: Date.now() / 1000,
        completed_at: null,
        current_agent: null,
        cost_usd: 0,
        duration_s: null,
        stage_idx: -1,
        reject_reason: null,
      },
      ...prev,
    ]);
  }, []);

  const handleSelectRun = useCallback((runId: string) => {
    setActiveRunId(runId);
    setShowEngineerView(false);
  }, []);

  const handleNodeClick = useCallback((role: string) => {
    setSelectedAgent(role);
    setRightTab('artifact');
  }, []);

  const handleGateResolved = useCallback(() => {
    // SSE stream updates the graph automatically
  }, []);

  const handleCloseArtifact = useCallback(() => {
    setSelectedAgent(null);
    setRightTab('activity');
  }, []);

  // Auto-switch to evidence tab when a run completes
  useEffect(() => {
    if (runStatus === 'done' || runStatus === 'complete') {
      setRightTab('evidence');
    }
  }, [runStatus]);

  const isDone = runStatus === 'done' || runStatus === 'complete';
  const isBlocked = runStatus === 'blocked';
  const isReturned = runStatus === 'returned';

  return (
    <div className="flex h-screen overflow-hidden bg-[#0f1117] text-white">
      {/* ── Sidebar (Work Queue) ─────────────────────────────────────────── */}
      <aside className="w-64 shrink-0 border-r border-[#2a3349] flex flex-col overflow-hidden">
        <WorkQueue
          runs={runs}
          activeRunId={activeRunId}
          onSelect={handleSelectRun}
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
              {connected && (
                <span className="flex items-center gap-1 text-[10px] text-blue-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                  Live
                </span>
              )}
              {sseError && (
                <span className="text-[10px] text-red-400">{sseError}</span>
              )}
              {isBlocked && (
                <span className="text-[10px] text-rose-400 font-semibold">▲ Blocked</span>
              )}
              {isReturned && (
                <span className="text-[10px] text-rose-400 font-semibold">↩ Returned</span>
              )}
              {isDone && (
                <span className="text-[10px] text-emerald-400 font-semibold">✓ Done</span>
              )}

              {/* Toggle: Spine / Graph */}
              <div className="ml-auto flex items-center gap-1 text-[10px]">
                <button
                  onClick={() => setShowEngineerView(false)}
                  className={`px-2 py-1 rounded transition-colors ${!showEngineerView ? 'text-blue-400 bg-[#1e2535]' : 'text-slate-500 hover:text-slate-300'}`}
                >
                  ☰ stages
                </button>
                <button
                  onClick={() => setShowEngineerView(true)}
                  className={`px-2 py-1 rounded transition-colors ${showEngineerView ? 'text-blue-400 bg-[#1e2535]' : 'text-slate-500 hover:text-slate-300'}`}
                >
                  ⬡ graph
                </button>
              </div>
            </>
          ) : (
            <span className="text-xs text-slate-600">Select or start a run</span>
          )}
        </header>

        {/* Graph + right panel row */}
        <div className="flex flex-1 overflow-hidden">
          {/* ── Pipeline view (Spine primary / AgentGraph secondary) ─────── */}
          <div className="flex-1 overflow-hidden">
            {activeRunId ? (
              showEngineerView ? (
                <AgentGraph events={events} onNodeClick={handleNodeClick} />
              ) : (
                <Spine
                  events={events}
                  runStatus={runStatus}
                  onNodeClick={handleNodeClick}
                  onToggleEngineerView={() => setShowEngineerView(true)}
                />
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
              {/* Gate inbox */}
              <GateInbox
                pendingGates={pendingGates}
                onResolved={handleGateResolved}
              />

              {/* Blocked panel */}
              {isBlocked && (
                <Blocked
                  runId={activeRunId}
                  gateName={blockedInfo?.gateName}
                  errorMessage={blockedInfo?.errorMessage}
                  onResolved={() => setRightTab('activity')}
                />
              )}

              {/* Returned panel */}
              {isReturned && (
                <Returned
                  runId={activeRunId}
                  reason={activeRun?.reject_reason}
                  onBackToQueue={() => setActiveRunId(null)}
                />
              )}

              {/* Tab bar */}
              <div className="flex border-b border-[#2a3349] shrink-0">
                {(['activity', 'artifact', 'evidence'] as RightTab[]).map((tab) => {
                  const label =
                    tab === 'activity' ? `Activity (${events.length})`
                    : tab === 'artifact' ? `Artifact${selectedAgent ? `: ${selectedAgent}` : ''}`
                    : 'Evidence';
                  return (
                    <button
                      key={tab}
                      onClick={() => setRightTab(tab)}
                      className={`flex-1 py-2 text-[10px] font-medium capitalize transition-colors
                        ${rightTab === tab
                          ? 'text-blue-400 border-b-2 border-blue-500'
                          : 'text-slate-500 hover:text-slate-300'}`}
                    >
                      {label}
                    </button>
                  );
                })}
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
                  <div>
                    <EvidenceStack runId={activeRunId} />
                    {activeRun && <RunMetrics run={activeRun} />}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
