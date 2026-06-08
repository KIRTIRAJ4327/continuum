"""
ci_gate.py -- block-on-regression eval gate for CI.

Behaviour:
  1. Load evals/results/baseline.json.
  2. If no baseline exists: run the CI suite, write baseline.json, exit 0.
  3. If baseline exists: run the CI suite, compare per-metric scores.
  4. If any metric regresses > REGRESSION_THRESHOLD vs baseline: exit 1.
  5. Otherwise: exit 0 (optionally update baseline with --update-baseline).

Metrics compared:
  - det_weighted     (deterministic scorer weighted average)
  - per_trial_rate   (fraction of trials that pass deterministic gate)
  - pass_k_rate      (fraction of cases where ALL k trials pass)
  - judge_avg        (average judge score)

Usage:
    # First run — writes baseline, exits 0:
    python evals/ci_gate.py

    # Subsequent runs — compares to baseline:
    python evals/ci_gate.py

    # After intentional improvement, update baseline:
    python evals/ci_gate.py --update-baseline
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

# Force UTF-8 output.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from evals.scorers import deterministic, judge  # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

logging.basicConfig(level=logging.WARNING)

_GOLDEN_DIR  = _ROOT / "evals" / "golden"
_RESULTS_DIR = _ROOT / "evals" / "results"
_BASELINE    = _RESULTS_DIR / "baseline.json"

# Regression > 2 percentage points blocks CI.
REGRESSION_THRESHOLD = 0.02

# CI runs k=3 on 5 representative cases (one per tier + two medium).
_CI_CASE_IDS = ["crud-01", "medium-01", "medium-03", "complex-01", "complex-03"]
_CI_K        = 3


def _load_golden_subset(case_ids: List[str]) -> tuple[List[Dict], Dict]:
    requests: List[Dict] = json.loads((_GOLDEN_DIR / "requests.json").read_text(encoding="utf-8"))
    labels:   Dict       = json.loads((_GOLDEN_DIR / "labels.json").read_text(encoding="utf-8"))
    filtered = [r for r in requests if r["id"] in case_ids]
    return filtered, labels


async def _run_trial(request: str, ctx: AgentContext) -> ContinuumState:
    state = ContinuumState(request=request)
    for role in ("bsa", "architect", "planner", "developer", "security"):
        await run_agent(state, role, ctx)
        if state.human_approval_pending:
            break
    return state


async def _run_ci_suite(ctx: AgentContext) -> Dict[str, Any]:
    """Run the CI subset and return aggregate metrics."""
    requests, labels = _load_golden_subset(_CI_CASE_IDS)

    det_scores:   List[float] = []
    judge_scores: List[float] = []
    trial_passes: int = 0
    total_trials: int = 0
    case_pass_k:  int = 0

    for case in requests:
        cid     = case["id"]
        req     = case["request"]
        label   = labels.get(cid, {})
        k_pass  = 0

        for _ in range(_CI_K):
            state  = await _run_trial(req, ctx)
            det    = deterministic.score(state, label)
            jdg    = await judge.score(state, label)
            det_scores.append(det["weighted"])
            judge_scores.append(jdg["value"])
            total_trials += 1
            if det["pass"]:
                trial_passes += 1
                k_pass += 1

        if k_pass == _CI_K:
            case_pass_k += 1

    n = len(requests)
    return {
        "det_weighted":   round(sum(det_scores) / len(det_scores), 4) if det_scores else 0.0,
        "per_trial_rate": round(trial_passes / total_trials, 4)        if total_trials else 0.0,
        "pass_k_rate":    round(case_pass_k / n, 4)                    if n else 0.0,
        "judge_avg":      round(sum(judge_scores) / len(judge_scores), 4) if judge_scores else 0.0,
        "n_cases":        n,
        "k":              _CI_K,
        "timestamp":      int(time.time()),
    }


def _load_baseline() -> Optional[Dict[str, Any]]:
    if _BASELINE.exists():
        try:
            return json.loads(_BASELINE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_baseline(metrics: Dict[str, Any]) -> None:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINE.write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def _compare(current: Dict[str, Any], baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return a list of regressions.  Each entry has:
        metric, baseline_val, current_val, delta, regressed (bool)
    """
    _TRACKED = ["det_weighted", "per_trial_rate", "pass_k_rate", "judge_avg"]
    rows = []
    for metric in _TRACKED:
        bval = baseline.get(metric, 0.0)
        cval = current.get(metric, 0.0)
        delta = cval - bval
        rows.append({
            "metric":       metric,
            "baseline":     bval,
            "current":      cval,
            "delta":        round(delta, 4),
            "regressed":    delta < -REGRESSION_THRESHOLD,
        })
    return rows


async def main(args: argparse.Namespace) -> int:
    ctx      = AgentContext(repo_path=".")
    baseline = _load_baseline()

    print("\nContinuum CI gate")
    print("=" * 60)
    print(f"Cases: {_CI_CASE_IDS}")
    print(f"k={_CI_K}  threshold={REGRESSION_THRESHOLD*100:.0f}pp regression blocks")
    print()

    print("Running CI eval suite...")
    t0 = time.time()
    current = await _run_ci_suite(ctx)
    elapsed = round(time.time() - t0, 1)
    print(f"Done in {elapsed}s\n")

    _fmt = lambda v: f"{v:.4f} ({v:.0%})"  # noqa: E731
    print(f"  det_weighted  : {_fmt(current['det_weighted'])}")
    print(f"  per_trial_rate: {_fmt(current['per_trial_rate'])}")
    print(f"  pass_k_rate   : {_fmt(current['pass_k_rate'])}")
    print(f"  judge_avg     : {_fmt(current['judge_avg'])}")

    if baseline is None:
        print("\nNo baseline found -- writing initial baseline and exiting 0.")
        _save_baseline(current)
        print(f"Baseline written to {_BASELINE.relative_to(_ROOT)}")
        return 0

    print(f"\nComparing to baseline (written {time.strftime('%Y-%m-%d', time.localtime(baseline.get('timestamp', 0)))})")
    comparisons = _compare(current, baseline)

    regressions = [c for c in comparisons if c["regressed"]]
    improvements = [c for c in comparisons if c["delta"] > REGRESSION_THRESHOLD]

    print()
    for c in comparisons:
        arrow = "↓ REGRESS" if c["regressed"] else ("↑ improve" if c["delta"] > 0.001 else "  stable ")
        print(f"  {arrow}  {c['metric']:20s}  baseline={c['baseline']:.4f}  current={c['current']:.4f}  delta={c['delta']:+.4f}")

    # Save current run for audit trail.
    ts = int(time.time())
    audit_path = _RESULTS_DIR / f"ci_{ts}.json"
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({"current": current, "baseline": baseline, "comparisons": comparisons}, indent=2),
        encoding="utf-8",
    )

    if regressions:
        print(f"\nFAIL: {len(regressions)} metric(s) regressed > {REGRESSION_THRESHOLD*100:.0f}pp vs baseline:")
        for r in regressions:
            print(f"  - {r['metric']}: {r['baseline']:.4f} -> {r['current']:.4f} ({r['delta']:+.4f})")
        return 1

    print("\nPASS: no regressions detected.")

    if args.update_baseline:
        _save_baseline(current)
        print(f"Baseline updated: {_BASELINE.relative_to(_ROOT)}")
    elif improvements:
        print(f"Tip: {len(improvements)} metric(s) improved -- run with --update-baseline to lock in gains.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuum CI regression gate")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="After a clean run, overwrite baseline.json with current results",
    )
    sys.exit(asyncio.run(main(parser.parse_args())))
