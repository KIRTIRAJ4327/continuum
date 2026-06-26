import { useEffect, useRef, useState } from 'react';
import type { AgentEvent } from '../types';

interface UseSSEResult {
  events: AgentEvent[];
  connected: boolean;
  error: string | null;
  reconnecting: boolean;
}

/**
 * Opens an EventSource to GET /events/{runId} and accumulates all events.
 * Automatically closes when runId changes or component unmounts.
 *
 * C1: the server stamps every frame with `id: <seq>`. The browser remembers the
 * last id and re-sends it as `Last-Event-ID` on automatic reconnect, so a dropped
 * connection resumes from the next unseen event — no duplicate replay. We therefore
 * do NOT close the connection in onerror (that would defeat the native retry);
 * we only surface a `reconnecting` flag while the browser re-establishes it.
 */
export function useSSE(runId: string | null): UseSSEResult {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const lastSeq = useRef<number>(-1);

  useEffect(() => {
    // Clear previous state when runId changes
    setEvents([]);
    setError(null);
    setConnected(false);
    setReconnecting(false);
    lastSeq.current = -1;

    if (!runId) return;

    const es = new EventSource(`/events/${runId}`);
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setReconnecting(false);
      setError(null);
    };

    es.onmessage = (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data as string) as AgentEvent;
        // Track the high-water seq; guard against any duplicate re-delivery.
        if (typeof ev.seq === 'number') {
          if (ev.seq <= lastSeq.current) return;
          lastSeq.current = ev.seq;
        }
        setEvents((prev) => [...prev, ev]);
        if (ev.event_type === 'run_complete') {
          es.close();
          setConnected(false);
          setReconnecting(false);
        }
      } catch {
        // ignore malformed frames (keepalive comments arrive as empty data)
      }
    };

    es.onerror = () => {
      // Do NOT close — let the browser auto-reconnect and resend Last-Event-ID.
      setConnected(false);
      setReconnecting(true);
      setError('SSE connection lost — reconnecting…');
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return { events, connected, error, reconnecting };
}
