import React, { useState } from 'react';
import type { AgentEvent } from '../types';
import { escalateResolve } from '../lib/api';

interface Props {
  runId: string;
  events: AgentEvent[];
}

/** Right-panel view shown when a run's status is `blocked` (gate stayed red). */
export function Blocked({ runId, events }: Props) {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  // Latest run_blocked event carries the failing gate + sensor output.
  const blockedEv = [...events].reverse().find((e) => e.event_type === 'run_blocked');
  const gateName = (blockedEv?.data?.gate_name as string) ?? 'local_verify';
  const sensorOut = (blockedEv?.data?.error_message as string) ?? '';

  // Fall back to the last gate_red output if no detail on the blocked event.
  const lastRed = [...events].reverse().find((e) => e.event_type === 'gate_red');
  const detail = sensorOut || (lastRed?.data?.output as string) || 'No sensor output captured.';

  async function sendBack() {
    setLoading(true);
    try {
      await escalateResolve(runId);
      setSent(true);
    } catch (err) {
      console.error('escalate-resolve failed', err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2a3349] shrink-0">
        <span className="text-rose-400 text-base">▲</span>
        <span className="text-xs font-semibold text-rose-300 uppercase tracking-wider">
          Blocked — {gateName}
        </span>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3">
        <p className="text-xs text-slate-400 leading-relaxed">
          The <span className="font-mono text-rose-300">{gateName}</span> gate stayed red past
          the retry budget (3 attempts). Review the sensor output, then send the run back to
          implementation to re-drive the developer chain.
        </p>

        <div>
          <p className="text-[10px] text-slate-500 uppercase mb-1">Sensor output</p>
          <pre className="bg-[#0f1117] border border-[#2a3349] rounded-lg p-3 text-[11px]
                          font-mono text-rose-200/80 overflow-auto whitespace-pre-wrap break-words
                          max-h-[40vh]">
            {detail}
          </pre>
        </div>

        <button
          onClick={sendBack}
          disabled={loading || sent}
          className="w-full py-2 rounded-lg bg-sky-700 hover:bg-sky-600 disabled:opacity-40
                     text-xs font-semibold text-white transition-colors"
        >
          {sent ? 'Sent back — re-driving…' : loading ? 'Sending…' : '↻  Send back to Implementation'}
        </button>
      </div>
    </div>
  );
}
