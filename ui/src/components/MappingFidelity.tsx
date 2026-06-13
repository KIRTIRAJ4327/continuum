import React from 'react';
import type { BusinessMapping, MappingFidelity as MappingFidelityData } from '../types';

interface Props {
  businessMappings?: BusinessMapping[];
  fidelity?: MappingFidelityData | null;
}

/** M7: Two-column supplied-vs-found table with exact-match pill / mismatch warning. */
export function MappingFidelity({ businessMappings, fidelity }: Props) {
  if (!businessMappings?.length) return null;

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
        Mapping Fidelity
      </p>

      {!fidelity ? (
        <p className="text-xs text-slate-600">Scope guard not yet run.</p>
      ) : (
        <>
          {/* Status pill */}
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
              fidelity.exact_match
                ? 'bg-emerald-900/50 text-emerald-300'
                : 'bg-rose-900/50 text-rose-300'
            }`}
          >
            {fidelity.exact_match ? '✓ Exact match' : '✗ Mismatch'}
          </span>

          {/* Supplied-vs-found table */}
          <table className="w-full text-xs border-collapse mt-1">
            <thead>
              <tr className="text-[9px] text-slate-500 uppercase tracking-wide">
                <th className="text-left pb-1 pr-2">Code</th>
                <th className="text-left pb-1 pr-2">Label</th>
                <th className="text-center pb-1">Found</th>
              </tr>
            </thead>
            <tbody>
              {businessMappings.map((m) => {
                const found = fidelity.found.includes(m.code);
                return (
                  <tr key={m.code} className="border-t border-[#2a3349]">
                    <td className="py-1 pr-2 font-mono text-slate-300">{m.code}</td>
                    <td className="py-1 pr-2 text-slate-400">{m.label}</td>
                    <td className={`py-1 text-center ${found ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {found ? '✓' : '✗'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Extra codes not in supplied list */}
          {fidelity.extra_in_code.length > 0 && (
            <p className="text-[10px] text-rose-400 mt-1">
              Extra in code: {fidelity.extra_in_code.join(', ')}
            </p>
          )}

          {/* Missing codes */}
          {fidelity.missing_in_code.length > 0 && (
            <p className="text-[10px] text-amber-400 mt-1">
              Missing: {fidelity.missing_in_code.join(', ')}
            </p>
          )}
        </>
      )}
    </div>
  );
}
