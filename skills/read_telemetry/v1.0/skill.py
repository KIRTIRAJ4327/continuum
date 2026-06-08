"""read_telemetry skill — summarise event-bus history for the Evolution Agent."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


async def read_telemetry(window_runs: str = "50") -> Dict[str, Any]:
    """
    Read the in-memory event bus and return a structured telemetry summary.

    Returns:
        {
          failure_rate_by_agent: {agent: fraction},
          gate_failures:         [{gate, agent, count, fraction}],
          avg_retries_by_gate:   {gate: avg_retry_count},
          human_escalations:     int,
          total_runs:            int,
          total_events:          int,
        }
    """
    limit = int(window_runs) if str(window_runs).isdigit() else 50

    try:
        from orchestrator.events import event_bus
        raw_log: Dict[str, List[Dict]] = dict(event_bus._log)
    except Exception:
        raw_log = {}

    if not raw_log:
        return _offline_summary()

    run_ids = list(raw_log.keys())[-limit:]
    filtered: Dict[str, List[Dict]] = {r: raw_log[r] for r in run_ids}

    agent_events:   Dict[str, int] = defaultdict(int)
    agent_failures: Dict[str, int] = defaultdict(int)
    gate_failures:  Dict[tuple, int] = defaultdict(int)
    gate_retries:   Dict[str, List[int]] = defaultdict(list)
    human_escl = 0

    for run_id, events in filtered.items():
        for ev in events:
            et    = ev.get("event_type", "")
            agent = ev.get("agent", "unknown")
            gate  = ev.get("gate", "")

            if et in ("agent_start", "agent_complete"):
                agent_events[agent] += 1

            if et == "gate_red":
                agent_failures[agent] += 1
                if gate:
                    gate_failures[(agent, gate)] += 1
                retry_count = (ev.get("data") or {}).get("retry_count", 0)
                if gate:
                    gate_retries[gate].append(int(retry_count))

            if et == "human_gate_opened":
                human_escl += 1

    failure_rate_by_agent = {
        agent: round(agent_failures[agent] / max(agent_events[agent], 1), 3)
        for agent in set(agent_events) | set(agent_failures)
    }

    gate_failure_list = [
        {
            "gate":     gate,
            "agent":    agent,
            "count":    count,
            "fraction": round(count / max(agent_events.get(agent, 1), 1), 3),
        }
        for (agent, gate), count in sorted(gate_failures.items(), key=lambda x: -x[1])
    ]

    avg_retries = {
        gate: round(sum(vals) / len(vals), 2)
        for gate, vals in gate_retries.items()
        if vals
    }

    return {
        "failure_rate_by_agent": failure_rate_by_agent,
        "gate_failures":         gate_failure_list,
        "avg_retries_by_gate":   avg_retries,
        "human_escalations":     human_escl,
        "total_runs":            len(filtered),
        "total_events":          sum(len(v) for v in filtered.values()),
    }


def _offline_summary() -> Dict[str, Any]:
    """Return a deterministic stub when no live event history is available."""
    return {
        "failure_rate_by_agent": {"frontend": 0.60, "bsa": 0.40, "security": 0.10},
        "gate_failures": [
            {"gate": "local_verify", "agent": "frontend", "count": 3, "fraction": 0.60},
            {"gate": "contract_validate", "agent": "architect", "count": 1, "fraction": 0.20},
        ],
        "avg_retries_by_gate": {"local_verify": 1.5},
        "human_escalations":   0,
        "total_runs":          0,
        "total_events":        0,
        "source":              "offline_stub",
    }
