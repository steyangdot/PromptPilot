"""Run N gated_session passes for a given chain.

The gated_session variant uses a Haiku referential classifier to gate
load_recent_turns(): it skips loading session history on prompts that
do NOT back-reference prior turns (per Item #3 in HANDOFF.md).

Used to validate the gating mechanism added on 2026-04-26:
    python extra_gated_runs.py --chain chain1 --start 1 --count 2   # regression
    python extra_gated_runs.py --chain chain4 --start 1 --count 3   # value demo

Refuses to overwrite existing gated_run<N>.json files.
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
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--chain", default="chain4")
    ap.add_argument("--tool", default="claude-code")
    args = ap.parse_args()

    chain = next(c for c in h.CHAINS if c["id"] == args.chain)
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wrap the whole loop so any zombies from a mid-loop crash get reaped on
    # exit, even if we hit Ctrl-C or an exception. Belt-and-suspenders with
    # the per-run reaper inside chain_test_v2.run_chain_once.
    with claude_subprocess_session("extra_gated_runs"):
        for offset in range(args.count):
            r = args.start + offset
            existing = out_dir / f"gated_session_run{r}.json"
            if existing.exists():
                raise SystemExit(f"Refusing to overwrite existing {existing}")
            print(f"\n=== Extra gated_session run {r} ({offset+1}/{args.count}) on {args.chain} ===")
            t0 = time.time()
            results = h.run_chain_once(chain, args.tool, "gated_session", r, out_dir)
            h.save_run(out_dir, "gated_session", r, results)
            dt = time.time() - t0
            scores = [t["score"]["success"] for t in results]
            skipped = sum(1 for t in results if t.get("gate_skipped"))
            print(f"  run{r} done in {dt:.0f}s  scores={scores}  mean={sum(scores)/len(scores):.3f}"
                  f"  gate_skipped={skipped}/{len(results)}")


if __name__ == "__main__":
    main()
