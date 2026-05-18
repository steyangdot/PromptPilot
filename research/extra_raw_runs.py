"""Run N raw passes for chain1/claude-code, numbering from --start.

Ground-zero baseline: no promptpilot, no SLM rewrite, no repo context, no session.
Just the user's raw prompt sent straight to claude-code. Used to measure the
full end-to-end lift of promptpilot (OPTIMIZED+SESSION vs RAW), as opposed to the
within-pipeline NO_SESSION vs WITH_SESSION comparison.

    python extra_raw_runs.py --start 1 --count 5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
from promptpilot._subprocess import claude_subprocess_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--chain", default="chain1")
    ap.add_argument("--tool", default="claude-code")
    args = ap.parse_args()

    chain = next(c for c in h.CHAINS if c["id"] == args.chain)
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    with claude_subprocess_session("extra_raw_runs"):
        for offset in range(args.count):
            r = args.start + offset
            existing = out_dir / f"raw_run{r}.json"
            if existing.exists():
                raise SystemExit(f"Refusing to overwrite existing {existing}")
            print(f"\n=== Extra raw run {r} ({offset+1}/{args.count}) ===")
            t0 = time.time()
            results = h.run_chain_once(chain, args.tool, "raw", r, out_dir)
            h.save_run(out_dir, "raw", r, results)
            dt = time.time() - t0
            scores = [t["score"]["success"] for t in results]
            print(f"  run{r} done in {dt:.0f}s  scores={scores}  mean={sum(scores)/len(scores):.3f}")


if __name__ == "__main__":
    main()
