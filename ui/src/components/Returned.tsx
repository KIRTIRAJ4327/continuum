import React from 'react';

interface Props {
  runId: string;
  reason?: string | null;
  onBackToQueue?: () => void;
}

export function Returned({ runId, reason, onBackToQueue }: Props) {
  return (
    <div className="px-3 py-3 border-b border-[#2a3349]">
      <div className="border border-rose-500/40 bg-[#2a0d0d] rounded-xl p-4 space-y-3">
        {/* Badge */}
        <div className="flex items-center gap-2">
          <span className="text-rose-400 text-base">↩</span>
          <span className="text-xs font-semibold text-rose-300 uppercase tracking-wider">
            Run Returned
          </span>
          <span className="ml-auto text-[10px] text-slate-500 font-mono">{runId}</span>
        </div>

        <p className="text-xs text-slate-400 leading-relaxed">
          This run was rejected by a human reviewer and sent back for revision.
        </p>

        {reason && (
          <div className="bg-[#1e0d0d] rounded p-2">
            <p className="text-[10px] text-slate-500 mb-1 uppercase tracking-wider">Reason</p>
            <p className="text-xs text-rose-200 leading-relaxed">{reason}</p>
          </div>
        )}

        {onBackToQueue && (
          <button
            onClick={onBackToQueue}
            className="w-full py-2 rounded-lg bg-[#1e2535] hover:bg-[#252d40]
                       text-xs font-medium text-slate-300 transition-colors"
          >
            ← Back to queue
          </button>
        )}
      </div>
    </div>
  );
}
