"""Recompute NO/WITH means for chain1/claude-code with flexible run selection.

Default behavior (when both --no-runs and --with-runs are omitted): skip run1
(for backwards compatibility with the original contamination case).

For the credit-exhaustion case (runs 1-2 contaminated, extra runs 6-7 added):
    python chain1_recompute_skipping_run1.py --no-runs 3,4,5,6,7 --with-runs 1,2,3,4,5
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

CHAIN_DIR = Path(__file__).parent / "chain_results_v2" / "claude-code" / "chain1"


def load_run(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def per_turn_successes(runs: list[list[dict]]) -> list[list[float]]:
    if not runs:
        return []
    n_turns = len(runs[0])
    return [[r[t]["score"]["success"] for r in runs] for t in range(n_turns)]


def summarize(label: str, runs: list[list[dict]]) -> float:
    if not runs:
        print(f"  [{label}] no runs")
        return float("nan")
    per_turn = per_turn_successes(runs)
    turn_means = [statistics.mean(vs) for vs in per_turn]
    run_means = [statistics.mean([t["score"]["success"] for t in r]) for r in runs]
    overall = statistics.mean(run_means)
    print(f"  [{label}] n_runs={len(runs)}")
    print(f"    per-run means: {[round(m,3) for m in run_means]}")
    print(f"    per-turn means: {[round(m,3) for m in turn_means]}")
    print(f"    overall mean: {overall:.3f}")
    if len(run_means) >= 2:
        print(f"    run-mean stdev: {statistics.stdev(run_means):.3f}")
    return overall


def parse_runs(spec: str) -> list[int]:
    return [int(x) for x in spec.split(",") if x.strip()]


def collect(chain_dir: Path, variant: str, selected: list[int] | None,
            include_run1: bool) -> tuple[list[Path], list[list[dict]]]:
    files = sorted(chain_dir.glob(f"{variant}_run*.json"))
    if selected is not None:
        want = set(selected)
        files = [p for p in files
                 if int(p.stem.split("run")[-1]) in want]
    elif not include_run1:
        files = [p for p in files if "run1.json" not in p.name]
    runs = [load_run(p) for p in files]
    return files, runs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-run1", action="store_true",
                    help="(Legacy) keep run1 when no --no-runs/--with-runs given")
    ap.add_argument("--no-runs", type=parse_runs, default=None,
                    help="Comma-separated run indices for no_session (e.g. 3,4,5,6,7)")
    ap.add_argument("--with-runs", type=parse_runs, default=None,
                    help="Comma-separated run indices for with_session")
    ap.add_argument("--dir", default=str(CHAIN_DIR))
    args = ap.parse_args()

    chain_dir = Path(args.dir)
    if not chain_dir.exists():
        raise SystemExit(f"Chain dir not found: {chain_dir}")

    no_files, no_runs = collect(chain_dir, "no_session", args.no_runs, args.include_run1)
    with_files, with_runs = collect(chain_dir, "with_session", args.with_runs, args.include_run1)

    if args.no_runs is None and args.with_runs is None and not args.include_run1:
        print("Skipping run1 (use --include-run1, or pass --no-runs/--with-runs for precise selection)")
    print(f"Reading from: {chain_dir}")
    print(f"  no_session files: {[p.name for p in no_files]}")
    print(f"  with_session files: {[p.name for p in with_files]}")
    print()

    no_mean = summarize("NO_SESSION", no_runs)
    print()
    with_mean = summarize("WITH_SESSION", with_runs)
    print()

    if no_runs and with_runs:
        delta = with_mean - no_mean
        pct = (delta / no_mean * 100) if no_mean else float("inf")
        print(f"delta (with - no) = {delta:+.3f}  ({pct:+.1f}% of no_session mean)")


if __name__ == "__main__":
    main()
