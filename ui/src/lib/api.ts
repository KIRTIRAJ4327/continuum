import type { RunSummary, EvidenceLayer } from '../types';

const BASE = '';  // Vite proxy routes to http://localhost:8000

export async function startRun(
  request: string,
  businessMappings: Array<{ code: string; label: string }> = [],
): Promise<{ run_id: string }> {
  const res = await fetch(`${BASE}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request, business_mappings: businessMappings }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listRuns(): Promise<RunSummary[]> {
  const res = await fetch(`${BASE}/runs`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getRun(runId: string) {
  const res = await fetch(`${BASE}/run/${runId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function resumeRun(runId: string, approved: boolean): Promise<void> {
  const res = await fetch(`${BASE}/run/${runId}/resume?approved=${approved}`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function rejectRun(runId: string, gate: string, reason: string): Promise<void> {
  const res = await fetch(`${BASE}/run/${runId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gate, reason }),
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function escalateResolve(runId: string): Promise<void> {
  const res = await fetch(`${BASE}/run/${runId}/escalate-resolve`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function getEvidence(runId: string): Promise<EvidenceLayer[]> {
  const res = await fetch(`${BASE}/runs/${runId}/evidence`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getArtifacts(runId: string, agent: string) {
  const res = await fetch(`${BASE}/artifacts/${runId}/${agent}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
