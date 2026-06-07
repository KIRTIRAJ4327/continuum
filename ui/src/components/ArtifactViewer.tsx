import React, { useEffect, useState } from 'react';
import { getArtifacts } from '../lib/api';

interface Props {
  runId: string;
  agent: string | null;
  onClose: () => void;
}

const AGENT_LABELS: Record<string, string> = {
  bsa:       'BSA — User Story',
  architect: 'Architect — Contract / Schema / DAG',
  planner:   'Planner — Execution Plan',
  developer: 'Developer — Code',
  security:  'Security — SAST Gate',
};

function CodeBlock({ content }: { content: string }) {
  return (
    <pre className="bg-[#0f1117] border border-[#2a3349] rounded-lg p-3 text-[11px]
                    font-mono text-slate-300 overflow-auto whitespace-pre-wrap break-words
                    max-h-[60vh]">
      {content}
    </pre>
  );
}

function FileTree({ code }: { code: Record<string, string> }) {
  const [selected, setSelected] = useState<string | null>(Object.keys(code)[0] ?? null);
  const files = Object.keys(code);
  return (
    <div className="flex gap-3 h-full">
      {/* File list */}
      <div className="w-48 shrink-0 overflow-y-auto border-r border-[#2a3349] pr-2 space-y-0.5">
        {files.map((f) => (
          <button
            key={f}
            onClick={() => setSelected(f)}
            className={`w-full text-left px-2 py-1 rounded text-[10px] font-mono truncate
                        hover:bg-[#252d40] transition-colors
                        ${selected === f ? 'bg-[#1e2535] text-blue-300' : 'text-slate-500'}`}
          >
            {f}
          </button>
        ))}
      </div>
      {/* File content */}
      <div className="flex-1 overflow-auto">
        {selected && <CodeBlock content={code[selected]} />}
      </div>
    </div>
  );
}

function renderArtifacts(agent: string, data: Record<string, unknown>) {
  const artifacts = (data.artifacts ?? {}) as Record<string, unknown>;

  if (agent === 'bsa') {
    const story = artifacts.story as Record<string, unknown> | null;
    if (!story) return <p className="text-slate-500 text-xs">No story yet.</p>;
    return (
      <div className="space-y-3">
        <div>
          <p className="text-[10px] text-slate-500 uppercase mb-1">Title</p>
          <p className="text-sm text-slate-200">{story.title as string}</p>
        </div>
        <div>
          <p className="text-[10px] text-slate-500 uppercase mb-1">Description</p>
          <p className="text-xs text-slate-400 leading-relaxed">{story.description as string}</p>
        </div>
        {Boolean(story.spec) && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase mb-1">Spec</p>
            <CodeBlock content={JSON.stringify(story.spec, null, 2)} />
          </div>
        )}
      </div>
    );
  }

  if (agent === 'architect') {
    return (
      <div className="space-y-4">
        {Boolean(artifacts.contract) && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase mb-1">OpenAPI Contract</p>
            <CodeBlock content={artifacts.contract as string} />
          </div>
        )}
        {Boolean(artifacts.schema) && (
          <div>
            <p className="text-[10px] text-slate-500 uppercase mb-1">Database Schema</p>
            <CodeBlock content={artifacts.schema as string} />
          </div>
        )}
      </div>
    );
  }

  if (agent === 'developer' || agent === 'database' || agent === 'backend' || agent === 'frontend') {
    const code = artifacts.code as Record<string, string> | null;
    if (!code || Object.keys(code).length === 0) {
      return <p className="text-slate-500 text-xs">No code files yet.</p>;
    }
    return <FileTree code={code} />;
  }

  if (agent === 'security') {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className={`text-sm ${artifacts.gate_status === 'green' ? 'text-green-400' : 'text-red-400'}`}>
            {artifacts.gate_status === 'green' ? '✓ Gate passed' : '✗ Gate failed'}
          </span>
        </div>
        {Boolean(artifacts.error) && (
          <CodeBlock content={artifacts.error as string} />
        )}
      </div>
    );
  }

  return <CodeBlock content={JSON.stringify(artifacts, null, 2)} />;
}

export function ArtifactViewer({ runId, agent, onClose }: Props) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agent) return;
    setLoading(true);
    setError(null);
    setData(null);
    getArtifacts(runId, agent)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId, agent]);

  if (!agent) return null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2a3349] shrink-0">
        <span className="text-xs font-semibold text-slate-300 flex-1 truncate">
          {AGENT_LABELS[agent] ?? agent.toUpperCase()}
        </span>
        <button
          onClick={onClose}
          className="text-slate-500 hover:text-slate-300 text-xs transition-colors"
        >
          ✕
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <span className="text-slate-600 text-xs animate-pulse">Loading artifact…</span>
          </div>
        )}
        {error && <p className="text-red-400 text-xs">{error}</p>}
        {data && renderArtifacts(agent, data)}
      </div>
    </div>
  );
}
