"""Run N extra with_session passes for chain1/claude-code, numbered from --start.

Used for improvement-validation: after changing MAX_TURNS=2->4 and making
record_to_session capture the downstream tool's modified files, run a couple
of extra WITH_SESSION passes to see if the signal (baseline WITH mean = 0.380
from runs 1-5) changes.

    python extra_with_session_runs.py --start 6 --count 2
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
from prpt._subprocess import claude_subprocess_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, required=True,
                    help="First run index to write (e.g. 6 if extending past run5)")
    ap.add_argument("--count", type=int, default=2,
                    help="How many additional runs to do")
    ap.add_argument("--chain", default="chain1")
    ap.add_argument("--tool", default="claude-code")
    args = ap.parse_args()

    chain = next(c for c in h.CHAINS if c["id"] == args.chain)
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wrap loop so any zombies from a mid-loop crash get reaped on exit.
    # See prpt/_subprocess.py for context.
    with claude_subprocess_session("extra_with_session_runs"):
        for offset in range(args.count):
            r = args.start + offset
            existing = out_dir / f"with_session_run{r}.json"
            if existing.exists():
                raise SystemExit(f"Refusing to overwrite existing {existing}")
            print(f"\n=== Extra with_session run {r} ({offset+1}/{args.count}) ===")
            t0 = time.time()
            results = h.run_chain_once(chain, args.tool, "with_session", r, out_dir)
            h.save_run(out_dir, "with_session", r, results)
            dt = time.time() - t0
            scores = [t["score"]["success"] for t in results]
            print(f"  run{r} done in {dt:.0f}s  scores={scores}  mean={sum(scores)/len(scores):.3f}")


if __name__ == "__main__":
    main()
