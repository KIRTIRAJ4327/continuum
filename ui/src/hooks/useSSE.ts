import { useEffect, useRef, useState } from 'react';
import type { AgentEvent } from '../types';

interface UseSSEResult {
  events: AgentEvent[];
  connected: boolean;
  error: string | null;
}

/**
 * Opens an EventSource to GET /events/{runId} and accumulates all events.
 * Automatically closes when runId changes or component unmounts.
 */
export function useSSE(runId: string | null): UseSSEResult {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Clear previous state when runId changes
    setEvents([]);
    setError(null);
    setConnected(false);

    if (!runId) return;

    const es = new EventSource(`/events/${runId}`);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setError(null);
    };

    es.onmessage = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data as string) as AgentEvent;
        setEvents((prev) => [...prev, ev]);
        if (ev.event_type === 'run_complete') {
          es.close();
          setConnected(false);
        }
      } catch {
        // ignore malformed frames (keepalive comments arrive as empty data)
      }
    };

    es.onerror = () => {
      setError('SSE connection lost');
      setConnected(false);
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return { events, connected, error };
}
