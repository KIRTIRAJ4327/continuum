import React, { useState } from 'react';
import { escalateResolve } from '../lib/api';

interface Props {
  runId: string;
  gateName?: string;
  errorMessage?: string;
  onResolved?: () => void;
}

export function Blocked({ runId, gateName, errorMessage, onResolved }: Props) {
  const [loading, setLoading] = useState(false);

  async function handleEscalateResolve() {
    setLoading(true);
    try {
      await escalateResolve(runId);
      onResolved?.();
    } catch (err) {
      console.error('Escalate-resolve failed', err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="px-3 py-3 border-b border-[#2a3349]">
      <div className="border border-rose-600/50 bg-[#2a0d0d] rounded-xl p-4 space-y-3">
        {/* Badge */}
        <div className="flex items-center gap-2">
          <span className="text-rose-400 text-base">▲</span>
          <span className="text-xs font-semibold text-rose-300 uppercase tracking-wider">
            Run Blocked
          </span>
        </div>

        <p className="text-xs text-slate-400 leading-relaxed">
          Automated verification exhausted all retries
          {gateName ? ` on gate <strong>${gateName}</strong>` : ''}.
          Review the output below and send back to implementation when ready.
        </p>

        {/* Sensor output */}
        {errorMessage && (
          <pre className="text-[10px] text-rose-300 bg-[#1e0d0d] rounded p-2 overflow-x-auto
                          max-h-32 whitespace-pre-wrap leading-snug">
            {errorMessage}
          </pre>
        )}

        <button
          onClick={handleEscalateResolve}
          disabled={loading}
          className="w-full py-2 rounded-lg bg-rose-800 hover:bg-rose-700 disabled:opacity-40
                     text-xs font-semibold text-white transition-colors"
        >
          {loading ? 'Sending back…' : '↩  Send back to Implementation'}
        </button>
      </div>
    </div>
  );
}
