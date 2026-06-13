import React, { useEffect, useState } from 'react';
import type { EvidenceLayer } from '../types';
import { getEvidence } from '../lib/api';

interface Props {
  runId: string;
}

const STATUS_ICON: Record<string, string> = {
  pass:    '✓',
  fail:    '✗',
  pending: '○',
};

const STATUS_COLOR: Record<string, string> = {
  pass:    'text-emerald-400',
  fail:    'text-rose-400',
  pending: 'text-slate-500',
};

export function EvidenceStack({ runId }: Props) {
  const [layers, setLayers] = useState<EvidenceLayer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getEvidence(runId)
      .then(setLayers)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) {
    return (
      <div className="px-4 py-6 text-xs text-slate-500 text-center animate-pulse">
        Loading evidence…
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-4 text-xs text-red-400">
        Evidence unavailable: {error}
      </div>
    );
  }

  const passCount = layers.filter((l) => l.status === 'pass').length;

  return (
    <div className="px-3 py-3 space-y-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
          Evidence Stack
        </span>
        <span className={`text-[10px] font-semibold ${passCount === layers.length ? 'text-emerald-400' : 'text-amber-400'}`}>
          {passCount}/{layers.length}
        </span>
      </div>

      {/* Layers */}
      {layers.map((layer) => (
        <div
          key={layer.layer}
          className="border border-[#2a3349] bg-[#161b27] rounded-lg px-3 py-2 space-y-0.5"
        >
          <div className="flex items-center gap-2">
            <span className={`text-sm w-4 text-center shrink-0 ${STATUS_COLOR[layer.status] ?? 'text-slate-500'}`}>
              {STATUS_ICON[layer.status] ?? '·'}
            </span>
            <span className="text-xs font-medium text-slate-200 flex-1">{layer.name}</span>
            <span className={`text-[10px] uppercase tracking-wide font-semibold ${STATUS_COLOR[layer.status] ?? 'text-slate-500'}`}>
              {layer.status}
            </span>
          </div>
          <div className="flex items-center gap-2 pl-6">
            <span className="text-[10px] text-slate-500 italic">{layer.sub_label}</span>
          </div>
          {layer.detail && layer.status !== 'pass' && (
            <p className="text-[10px] text-slate-500 pl-6 leading-snug line-clamp-2">
              {layer.detail}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
