import React, { useState } from 'react';
import type { PendingGate } from '../types';
import { resumeRun, rejectRun } from '../lib/api';

interface Props {
  pendingGates: PendingGate[];
  onResolved: (gateName: string, approved: boolean) => void;
}

const GATE_DESCRIPTIONS: Record<string, { title: string; description: string }> = {
  story_review: {
    title: 'Story Review',
    description: 'The BSA has produced a user story and specification. Review the story before architecture begins.',
  },
  design_review: {
    title: 'Design Review',
    description: 'The Architect has produced an OpenAPI contract, database schema, and component DAG. Approve to start development.',
  },
  merge_review: {
    title: 'Merge Review',
    description: 'The pipeline has passed all automated gates. Approve to create the pull request.',
  },
  local_verify: {
    title: 'Local Verify Failed',
    description: 'The generated code failed automated verification after 3 retries. Review the errors and decide whether to retry.',
  },
};

interface CardProps {
  gate: PendingGate;
  onResolved: (gateName: string, approved: boolean) => void;
}

function GateCard({ gate, onResolved }: CardProps) {
  const [loading, setLoading] = useState<'approve' | 'reject' | null>(null);
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const meta = GATE_DESCRIPTIONS[gate.gate_name] ?? {
    title:       gate.gate_name,
    description: 'Human review required before continuing.',
  };

  async function handleApprove() {
    setLoading('approve');
    try {
      await resumeRun(gate.run_id, true);
      onResolved(gate.gate_name, true);
    } catch (err) {
      console.error('Resume failed', err);
    } finally {
      setLoading(null);
    }
  }

  async function handleReject() {
    const reason = rejectReason.trim() || 'Rejected by operator';
    setLoading('reject');
    try {
      await rejectRun(gate.run_id, gate.gate_name, reason);
      onResolved(gate.gate_name, false);
    } catch (err) {
      console.error('Reject failed', err);
    } finally {
      setLoading(null);
      setShowRejectForm(false);
      setRejectReason('');
    }
  }

  return (
    <div className="border border-amber-500/40 bg-[#2a1a00] rounded-xl p-4 space-y-3">
      {/* Badge */}
      <div className="flex items-center gap-2">
        <span className="text-amber-400 text-base">⏸</span>
        <span className="text-xs font-semibold text-amber-300 uppercase tracking-wider">
          {meta.title}
        </span>
        <span className="ml-auto text-[10px] text-slate-500 font-mono">{gate.run_id}</span>
      </div>

      <p className="text-xs text-slate-400 leading-relaxed">{meta.description}</p>

      {/* Reject reason form */}
      {showRejectForm && (
        <div className="space-y-2">
          <textarea
            className="w-full bg-[#1e2535] border border-[#2a3349] rounded-lg p-2 text-xs
                       text-slate-200 placeholder-slate-600 resize-none focus:outline-none
                       focus:border-rose-500 transition-colors"
            rows={2}
            placeholder="Reason for rejection…"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            autoFocus
          />
          <div className="flex gap-2">
            <button
              onClick={handleReject}
              disabled={loading !== null}
              className="flex-1 py-1.5 rounded-lg bg-rose-800 hover:bg-rose-700
                         disabled:opacity-40 text-xs font-semibold text-white transition-colors"
            >
              {loading === 'reject' ? 'Rejecting…' : '↩  Confirm reject'}
            </button>
            <button
              onClick={() => { setShowRejectForm(false); setRejectReason(''); }}
              disabled={loading !== null}
              className="px-3 py-1.5 rounded-lg bg-[#1e2535] hover:bg-[#252d40]
                         disabled:opacity-40 text-xs text-slate-400 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Primary actions */}
      {!showRejectForm && (
        <div className="flex gap-2 pt-1">
          <button
            onClick={handleApprove}
            disabled={loading !== null}
            className="flex-1 py-2 rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40
                       text-xs font-semibold text-white transition-colors"
          >
            {loading === 'approve' ? 'Approving…' : '✓  Approve'}
          </button>
          <button
            onClick={() => setShowRejectForm(true)}
            disabled={loading !== null}
            className="flex-1 py-2 rounded-lg bg-red-900 hover:bg-red-800 disabled:opacity-40
                       text-xs font-semibold text-slate-300 transition-colors"
          >
            ✗  Reject
          </button>
        </div>
      )}
    </div>
  );
}

export function GateInbox({ pendingGates, onResolved }: Props) {
  if (pendingGates.length === 0) return null;

  return (
    <div className="px-3 py-3 border-b border-[#2a3349] space-y-3">
      <p className="text-[10px] text-amber-400 uppercase tracking-widest font-semibold">
        Approval Required ({pendingGates.length})
      </p>
      {pendingGates.map((g) => (
        <GateCard key={`${g.run_id}-${g.gate_name}`} gate={g} onResolved={onResolved} />
      ))}
    </div>
  );
}
