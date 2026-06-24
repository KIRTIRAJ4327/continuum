"""
verify_p02_boxlite.py — P0.2 Box Lite verification (4/4).

Checks:
  A. write + exec → real returncode 0
  B. exec failure → returncode 1 captured
  C. exec timeout → returncode -1, process killed
  D. teardown → workdir gone

Usage:
    python scripts/verify_p02_boxlite.py
Expected:
    4/4 checks passed
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load BoxLite via the same importlib path the skill loader uses.
_skills_dir = Path(__file__).resolve().parent.parent / "skills" / "sandbox"
_versions = sorted((p for p in _skills_dir.glob("v*") if p.is_dir()), key=lambda p: p.name)
if not _versions:
    print("FAIL — skills/sandbox/v*/skill.py not found")
    sys.exit(1)
_spec = importlib.util.spec_from_file_location("continuum_skill_sandbox", _versions[-1] / "skill.py")
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["continuum_skill_sandbox"] = _mod   # needed for @dataclass to resolve __module__
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
BoxLite = _mod.BoxLite

_CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    _CHECKS.append((name, passed))


async def main() -> int:
    print()
    print("P0.2 Box Lite verification (4/4)")
    print("=" * 40)

    # ── Case A: write + exec → returncode 0 ──────────────────────────────── #
    print("\nCase A: write + exec → returncode 0")
    box = BoxLite(run_id="test-boxlite", agent="backend-a")
    await box.write("hello.py", "print('hello box')\n")
    r = await box.exec("python hello.py")
    _check(
        "write + exec → returncode 0",
        r.ok and "hello box" in r.stdout,
        r.summary(),
    )
    await box.teardown()

    # ── Case B: exec failure → returncode 1 ──────────────────────────────── #
    print("\nCase B: exec failure → returncode 1")
    box = BoxLite(run_id="test-boxlite", agent="backend-b")
    r = await box.exec("python -c 'import sys; sys.exit(1)'")
    _check(
        "exec failure → returncode 1",
        r.returncode == 1,
        r.summary(),
    )
    await box.teardown()

    # ── Case C: timeout → returncode -1, process killed ──────────────────── #
    print("\nCase C: timeout → returncode -1")
    box = BoxLite(run_id="test-boxlite", agent="backend-c")
    r = await box.exec("python -c 'import time; time.sleep(120)'", timeout=2)
    _check(
        "timeout → returncode -1",
        r.returncode == -1,
        r.summary(),
    )
    await box.teardown()

    # ── Case D: teardown → workdir gone ──────────────────────────────────── #
    print("\nCase D: teardown → workdir gone")
    box = BoxLite(run_id="test-boxlite", agent="backend-d")
    await box.write("marker.txt", "exists")
    workdir = box.workdir
    assert workdir.exists(), "workdir should exist before teardown"
    await box.teardown()
    _check(
        "teardown → workdir gone",
        not workdir.exists(),
        str(workdir),
    )

    # ── Summary ───────────────────────────────────────────────────────────── #
    print()
    print("=" * 40)
    passed = sum(1 for _, ok in _CHECKS if ok)
    total = len(_CHECKS)
    print(f"{passed}/{total} checks passed")
    print()
    if passed == total:
        print("P0.2 Box Lite: OK")
    else:
        print("FAIL — fix the checks above")
        for name, ok in _CHECKS:
            if not ok:
                print(f"  FAILED: {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
