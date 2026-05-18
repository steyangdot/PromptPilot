"""Arm B of 3-arm experiment: plain claude-code with --resume (built-in session only).

No promptpilot, no SLM optimizer. Raw user prompts sent to claude-code; claude-code's
own session persistence carries conversation across turns via --resume.

Run with USE_BUILTIN_SESSION=1:
    USE_BUILTIN_SESSION=1 python extra_builtin_runs.py --start 1 --count 5
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
from prpt._subprocess import claude_subprocess_session  # noqa: E402


def main() -> None:
    if os.environ.get("USE_BUILTIN_SESSION") != "1":
        raise SystemExit("Must set USE_BUILTIN_SESSION=1 in env for this script")
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--chain", default="chain1")
    ap.add_argument("--tool", default="claude-code")
    args = ap.parse_args()

    chain = next(c for c in h.CHAINS if c["id"] == args.chain)
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    with claude_subprocess_session("extra_builtin_runs"):
        for offset in range(args.count):
            r = args.start + offset
            existing = out_dir / f"builtin_run{r}.json"
            if existing.exists():
                raise SystemExit(f"Refusing to overwrite existing {existing}")
            print(f"\n=== Extra builtin run {r} ({offset+1}/{args.count}) ===")
            t0 = time.time()
            results = h.run_chain_once(chain, args.tool, "builtin", r, out_dir)
            h.save_run(out_dir, "builtin", r, results)
            dt = time.time() - t0
            scores = [t["score"]["success"] for t in results]
            print(f"  run{r} done in {dt:.0f}s  scores={scores}  mean={sum(scores)/len(scores):.3f}")


if __name__ == "__main__":
    main()
