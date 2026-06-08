"""
pass_k_runner.py -- measure pipeline reliability as pass^k.

pass^k for a single case: run it k times; pass^k = 1 iff ALL k trials pass.
Aggregate reliability = (cases where pass^k=1) / total_cases.

Math note:
  If per-trial pass rate is p, then pass^k = p^k.
  A 70%-per-trial pipeline gives pass^3 = 0.7^3 = 0.343 (~34%).
  Both per-trial and pass^k are reported so the difference is visible.

Usage:
    # Fast CI (k=3, 5 cases):
    python evals/pass_k_runner.py --k 3 --cases 5

    # Full release gate (k=5, all 20 cases):
    python evals/pass_k_runner.py --k 5

    # Single case:
    python evals/pass_k_runner.py --k 3 --filter crud-01

Output:
    evals/results/pass_k_<timestamp>.json
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force UTF-8 output so symbols survive Windows cp1252.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from evals.scorers import deterministic, judge, human  # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

logging.basicConfig(level=logging.WARNING)  # quiet except warnings+
logger = logging.getLogger(__name__)

_GOLDEN_DIR = _ROOT / "evals" / "golden"
_RESULTS_DIR = _ROOT / "evals" / "results"

# Pipeline roles run for each eval trial.
_PIPELINE_ROLES = ["bsa", "architect", "planner", "developer", "security"]

# Weights for the final combined score.
_W_DET   = 0.60
_W_JUDGE = 0.30
_W_HUMAN = 0.10


def _load_golden(
    n_cases: Optional[int] = None,
    filter_id: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load requests.json and labels.json, optionally filtered/truncated."""
    requests: List[Dict[str, Any]] = json.loads(
        (_GOLDEN_DIR / "requests.json").read_text(encoding="utf-8")
    )
    labels: Dict[str, Any] = json.loads(
        (_GOLDEN_DIR / "labels.json").read_text(encoding="utf-8")
    )
    if filter_id:
        requests = [r for r in requests if r["id"] == filter_id]
    if n_cases:
        requests = requests[:n_cases]
    return requests, labels


async def _run_once(request: str, ctx: AgentContext) -> ContinuumState:
    """Execute one pipeline trial and return final state."""
    state = ContinuumState(request=request)
    for role in _PIPELINE_ROLES:
        await run_agent(state, role, ctx)
        if state.human_approval_pending:
            break  # shouldn't happen offline
    return state


async def _score_state(
    case_id: str,
    request: str,
    state: ContinuumState,
    label: Dict[str, Any],
) -> Dict[str, Any]:
    """Run all scorers against a completed state and merge into one result."""
    det = deterministic.score(state, label)
    jdg = await judge.score(state, label)
    hum = human.score(
        case_id=case_id,
        request=request,
        det_score=det["weighted"],
        judge_result=jdg,
        story_title=(state.story or {}).get("title", ""),
    )

    # Weighted combination (drop human weight when None).
    w_det, w_jdg, w_hum = _W_DET, _W_JUDGE, _W_HUMAN
    if hum["value"] is None:
        # Redistribute human weight proportionally.
        total = w_det + w_jdg
        w_det = w_det / total
        w_jdg = w_jdg / total
        w_hum = 0.0

    combined = (
        det["weighted"] * w_det
        + jdg["value"] * w_jdg
        + (hum["value"] or 0.0) * w_hum
    )

    return {
        "deterministic": det,
        "judge": jdg,
        "human": hum,
        "combined": round(combined, 4),
        "pass": det["pass"],  # gate: must clear deterministic threshold
    }


async def run_case(
    case: Dict[str, Any],
    label: Dict[str, Any],
    k: int,
    ctx: AgentContext,
) -> Dict[str, Any]:
    """Run one golden case k times and compute pass^k statistics."""
    case_id  = case["id"]
    request  = case["request"]
    tier     = case.get("tier", "unknown")

    trials: List[Dict[str, Any]] = []
    trial_passes = 0

    print(f"  [{case_id}] tier={tier} k={k}")

    for i in range(k):
        t0 = time.time()
        state = await _run_once(request, ctx)
        elapsed = round(time.time() - t0, 2)
        trial_result = await _score_state(case_id, request, state, label)
        passed = trial_result["pass"]
        trial_passes += int(passed)
        symbol = "OK" if passed else "XX"
        print(f"    trial {i+1}/{k}: {symbol}  det={trial_result['deterministic']['weighted']:.2f}"
              f"  judge={trial_result['judge']['value']:.2f}  combined={trial_result['combined']:.2f}"
              f"  ({elapsed}s)")
        trials.append({
            "trial": i + 1,
            "elapsed_s": elapsed,
            **trial_result,
        })

    per_trial_rate = trial_passes / k
    pass_k = 1 if trial_passes == k else 0

    print(f"    -> per-trial={per_trial_rate:.0%}  pass^{k}={'PASS' if pass_k else 'FAIL'}"
          f"  (expected pass^{k} at this rate: {per_trial_rate**k:.0%})")

    return {
        "case_id":        case_id,
        "tier":           tier,
        "request":        request[:80],
        "k":              k,
        "trials":         trials,
        "trial_passes":   trial_passes,
        "per_trial_rate": round(per_trial_rate, 4),
        "pass_k":         pass_k,
        "expected_pass_k": round(per_trial_rate ** k, 4),
    }


async def main(args: argparse.Namespace) -> int:
    k          = args.k
    n_cases    = args.cases
    filter_id  = args.filter
    output     = args.output

    requests, labels = _load_golden(n_cases=n_cases, filter_id=filter_id)
    if not requests:
        print(f"No cases matched filter '{filter_id}'")
        return 1

    ctx = AgentContext(repo_path=".")

    print(f"\npass^{k} runner  --  {len(requests)} case(s)")
    print("=" * 60)

    t_start = time.time()
    case_results: List[Dict[str, Any]] = []

    for case in requests:
        label = labels.get(case["id"], {})
        result = await run_case(case, label, k=k, ctx=ctx)
        case_results.append(result)

    elapsed_total = round(time.time() - t_start, 1)

    # Aggregate.
    n_total  = len(case_results)
    n_pass_k = sum(r["pass_k"] for r in case_results)
    avg_ptr  = sum(r["per_trial_rate"] for r in case_results) / n_total

    print("\n" + "=" * 60)
    print(f"RESULTS  ({elapsed_total}s total)")
    print(f"  Cases:        {n_total}")
    print(f"  pass^{k}:       {n_pass_k}/{n_total}  ({n_pass_k/n_total:.0%})")
    print(f"  Avg per-trial: {avg_ptr:.0%}")
    print(f"  Expected pass^{k} at avg rate: {avg_ptr**k:.0%}")

    by_tier: Dict[str, List] = {}
    for r in case_results:
        by_tier.setdefault(r["tier"], []).append(r["pass_k"])
    print("\n  By tier:")
    for tier, passes in sorted(by_tier.items()):
        n = len(passes)
        p = sum(passes)
        print(f"    {tier:10s}: {p}/{n} pass^{k}")

    # Save results.
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = _RESULTS_DIR / (output or f"pass_k_{ts}.json")
    report = {
        "k":              k,
        "n_cases":        n_total,
        "pass_k_count":   n_pass_k,
        "pass_k_rate":    round(n_pass_k / n_total, 4),
        "avg_per_trial":  round(avg_ptr, 4),
        "elapsed_s":      elapsed_total,
        "timestamp":      ts,
        "cases":          case_results,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  Report written to: {out_path.relative_to(_ROOT)}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pass^k reliability runner")
    parser.add_argument("--k",      type=int, default=3,    help="Trials per case (default 3)")
    parser.add_argument("--cases",  type=int, default=None, help="Max cases to run (default all)")
    parser.add_argument("--filter", type=str, default=None, help="Run only this case id")
    parser.add_argument("--output", type=str, default=None, help="Output filename in evals/results/")
    sys.exit(asyncio.run(main(parser.parse_args())))
