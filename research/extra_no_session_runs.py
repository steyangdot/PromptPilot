"""Run N extra no_session passes for chain1/claude-code, numbering from --start.

Used to backfill runs contaminated by credit exhaustion: the first harness
pass's runs 1-2 had T1 bail on zero tokens. We keep runs 3-5 and add two more
as run6, run7 so we have 5 clean no_session runs matched against 5 with_session.

    python extra_no_session_runs.py --start 6 --count 2
"""
from __future__ import annotations

import argparse
import json
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

    with claude_subprocess_session("extra_no_session_runs"):
        for offset in range(args.count):
            r = args.start + offset
            # Guard: don't silently clobber an existing run file
            existing = out_dir / f"no_session_run{r}.json"
            if existing.exists():
                raise SystemExit(f"Refusing to overwrite existing {existing}")
            print(f"\n=== Extra no_session run {r} ({offset+1}/{args.count}) ===")
            t0 = time.time()
            results = h.run_chain_once(chain, args.tool, "no_session", r, out_dir)
            h.save_run(out_dir, "no_session", r, results)
            dt = time.time() - t0
            scores = [t["score"]["success"] for t in results]
            print(f"  run{r} done in {dt:.0f}s  scores={scores}  mean={sum(scores)/len(scores):.3f}")


if __name__ == "__main__":
    main()
