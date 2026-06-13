import React, { useEffect, useState } from 'react';
import type { EvidenceLayer, EvidenceStatus } from '../types';
import { getEvidence } from '../lib/api';

interface Props {
  runId: string;
}

const STATUS_META: Record<EvidenceStatus, { glyph: string; color: string; label: string }> = {
  pass:    { glyph: '✓', color: 'text-emerald-400', label: 'PASS' },
  fail:    { glyph: '✗', color: 'text-rose-400',    label: 'FAIL' },
  pending: { glyph: '·', color: 'text-slate-500',   label: '—'    },
};

/** The 6-layer Evidence Stack — independent proof a run is merge-ready (M6). */
export function EvidenceStack({ runId }: Props) {
  const [layers, setLayers] = useState<EvidenceLayer[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getEvidence(runId)
      .then((l) => alive && setLayers(l))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [runId]);

  if (error) return <p className="text-rose-400 text-xs px-4 py-2">{error}</p>;

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
        Evidence Stack
      </p>
      <div className="divide-y divide-[#2a3349] rounded-lg border border-[#2a3349]">
        {layers.map((layer, i) => {
          const meta = STATUS_META[layer.status] ?? STATUS_META.pending;
          return (
            <div key={i} className="flex items-start gap-3 px-3 py-2">
              <span className={`mt-0.5 w-3 text-center ${meta.color}`}>{meta.glyph}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-xs text-slate-200">{layer.layer}</p>
                  <span className={`text-[9px] font-mono ${meta.color}`}>{meta.label}</span>
                </div>
                {layer.sublabel && (
                  <p className="text-[10px] text-slate-600">{layer.sublabel}</p>
                )}
                <p className="text-[10px] text-slate-500 leading-snug truncate">{layer.detail}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
