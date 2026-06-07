import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RunList } from './components/RunList';
import { AgentGraph } from './components/AgentGraph';
import { ActivityStream } from './components/ActivityStream';
import { GateInbox } from './components/GateInbox';
import { ArtifactViewer } from './components/ArtifactViewer';
import { useSSE } from './hooks/useSSE';
import { listRuns } from './lib/api';
import type { RunSummary, PendingGate } from './types';

// Right panel tab modes
type RightTab = 'activity' | 'artifact';

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>('activity');
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  // ── Load run list on mount and poll for updates ───────────────────────────
  useEffect(() => {
    async function load() {
      try {
        const r = await listRuns();
        setRuns(r);
        // Auto-select the newest run if none selected
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
    setSelectedAgent(null);
    // Add optimistic entry to the run list
    setRuns((prev) => [
      {
        run_id: runId,
        request: '…',
        status: 'running',
        started_at: Date.now() / 1000,
        completed_at: null,
        current_agent: null,
      },
      ...prev,
    ]);
  }, []);

  const handleNodeClick = useCallback((role: string) => {
    setSelectedAgent(role);
    setRightTab('artifact');
  }, []);

  const handleGateResolved = useCallback((gateName: string, approved: boolean) => {
    console.log('Gate resolved', gateName, approved);
    // The SSE stream will emit human_gate_resolved and update the graph
  }, []);

  const handleCloseArtifact = useCallback(() => {
    setSelectedAgent(null);
    setRightTab('activity');
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-[#0f1117] text-white">
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside className="w-64 shrink-0 border-r border-[#2a3349] flex flex-col overflow-hidden">
        <RunList
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
              {connected && (
                <span className="flex items-center gap-1 text-[10px] text-blue-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                  Live
                </span>
              )}
              {sseError && (
                <span className="text-[10px] text-red-400">{sseError}</span>
              )}
            </>
          ) : (
            <span className="text-xs text-slate-600">Select or start a run</span>
          )}
        </header>

        {/* Graph + right panel row */}
        <div className="flex flex-1 overflow-hidden">
          {/* ── Pipeline graph ─────────────────────────────────────────── */}
          <div className="flex-1 overflow-hidden">
            {activeRunId ? (
              <AgentGraph events={events} onNodeClick={handleNodeClick} />
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
              {/* Gate inbox (always visible when there are pending gates) */}
              <GateInbox
                pendingGates={pendingGates}
                onResolved={handleGateResolved}
              />

              {/* Tab bar */}
              <div className="flex border-b border-[#2a3349] shrink-0">
                {(['activity', 'artifact'] as RightTab[]).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setRightTab(tab)}
                    className={`flex-1 py-2 text-xs font-medium capitalize transition-colors
                      ${rightTab === tab
                        ? 'text-blue-400 border-b-2 border-blue-500'
                        : 'text-slate-500 hover:text-slate-300'}`}
                  >
                    {tab === 'activity' ? `Activity (${events.length})` : `Artifact${selectedAgent ? `: ${selectedAgent}` : ''}`}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              <div className="flex-1 overflow-hidden">
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
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
