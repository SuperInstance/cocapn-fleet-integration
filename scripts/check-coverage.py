#!/usr/bin/env python3
"""Check coverage thresholds across all fleet Python repos."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
REPOS = [
    "sunset-ecosystem",
    "ccc-os",
    "cocapn-health",
    "cocapn-traps",
    "vector-novelty",
    "pareto-tournament",
    "hebbian-router",
    "cocapn-plato",
    "turbovec-integration-ccc",
]


def get_coverage(repo: str, threshold: float) -> tuple[bool, float | None]:
    """Run pytest with coverage and return (passes, percentage)."""
    path = REPO_ROOT / repo
    if not path.exists():
        return False, None

    # Check if coverage data exists from CI artifact
    cov_json = path / "coverage.json"
    if cov_json.exists():
        data = json.loads(cov_json.read_text())
        pct = data.get("totals", {}).get("percent_covered", 0)
        return pct >= threshold, pct

    # Run pytest with coverage
    result = subprocess.run(
        [
            "python3", "-m", "pytest",
            "--cov=src", "--cov-report=json:coverage.json",
            "-q", "--tb=no",
        ],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):  # 1 = test failures, still have coverage
        return False, None

    cov_json = path / "coverage.json"
    if not cov_json.exists():
        return False, None

    data = json.loads(cov_json.read_text())
    pct = data.get("totals", {}).get("percent_covered", 0)
    return pct >= threshold, pct


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=75.0)
    args = parser.parse_args()

    fail = 0
    for repo in REPOS:
        passes, pct = get_coverage(repo, args.threshold)
        if pct is None:
            print(f"SKIP  {repo:30} (no coverage data)")
            continue
        status = "PASS" if passes else "FAIL"
        print(f"{status}  {repo:30} {pct:.1f}% (threshold: {args.threshold:.0f}%)")
        if not passes:
            fail += 1

    print(f"\nTotal: {len(REPOS) - fail} passed, {fail} below threshold")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
