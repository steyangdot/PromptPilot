"""Verify-gate (OPTIMIZATION_LEVERS Lever #1) measurement: WITH_SESSION vs WITH_GATE.

Isolates the verify-gate's effect: BOTH arms are SLM-rewrite + bounded session; WITH_GATE
adds the post-turn gate (test the changed files -> capped, SLM-bypassed retry, with the retry
tokens CHARGED to the arm via run_verify_gate_turn). So the comparison is gate-vs-no-gate with
the rewrite and the session mechanism held constant.

Pre-registered metric basis (per the re-test corrections — write this BEFORE running):
  * SUCCESS  = END-STATE only, via score_endstate.py's captured-artifact scorer (capture_end_state
               writes endstate_{arm}_run{N}.json each run). NEVER the per-turn-diff score (unfit).
  * COST     = UNCACHED input tokens INCLUDING retry tokens (run_verify_gate_turn merges them) +
               cache-aware $/end-state-success. Gross input is ~95% cached -> never the headline.
  * Ship/keep the gate iff: end-state success non-inferior (mean drop <= 0.10, directional gate)
    AND it strictly fixes >=1 run that WITH_SESSION left broken, at a retry-cost the $/success
    still justifies. Falsifier: success drops > 0.10, or no run is rescued, or $/success worsens.
  * Design: interleaved arm order (A,B,A,B...) to kill time-of-day / provider-cache-warm confounds;
    serial (never two harness instances on one repo); repo reset per run; N>=5; same CLI version.

REQUIRES the seeded-bug fixture: C:/projects/httpx on branch `seeded-timeout-bug` @ dbeced8.
Real quota cost -> run as a ONE-SHOT Task-Scheduler job off-peak (operational lessons in the
handoff). Resume-aware: a completed run is loaded, not re-run, so a quota window that only fits
a few runs still makes forward progress.

    set CLAUDE_MODEL=claude-opus-4-8
    python research/verify_gate_experiment.py --runs 5 --tool claude-code

After it finishes:
    python research/score_endstate.py <OUT_BASE> --tool=claude-code --arms with_session,with_gate
    python research/analyze_uncached_cost.py <OUT_BASE> --tool claude-code
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
from prpt._subprocess import claude_subprocess_session  # noqa: E402

ARMS = ["with_session", "with_gate"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="Runs per arm (default 5)")
    ap.add_argument("--chain", default="chain1")
    ap.add_argument("--tool", default="claude-code", choices=["claude-code", "codex"])
    args = ap.parse_args()

    chain = next((c for c in h.CHAINS if c["id"] == args.chain), None)
    if chain is None:
        raise SystemExit("unknown chain: {0}".format(args.chain))
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("verify-gate experiment  arms={0}  runs={1}  chain={2}  tool={3}".format(
        ARMS, args.runs, chain["id"], args.tool))
    print("out_dir:", out_dir)
    print("fixture: C:/projects/httpx @ seeded-timeout-bug (verify HEAD before running)")
    print("=" * 78)

    completed = 0
    with claude_subprocess_session("verify_gate_experiment"):
        # Interleave A,B per run index so the two arms see matched conditions.
        for r in range(1, args.runs + 1):
            for variant in ARMS:
                cached = h.load_run(out_dir, variant, r)
                if cached is not None:
                    print("  [resume] {0} run {1} already complete -> load".format(variant, r))
                    completed += 1
                    continue
                print("\n--- {0} run {1}/{2} ---".format(variant, r, args.runs))
                try:
                    results = h.run_chain_once(chain, args.tool, variant, r, out_dir)
                except h.QuotaExhausted as e:
                    print("\n[abort] {0}".format(e))
                    print("Resume after the quota window resets — completed runs are kept.")
                    raise SystemExit(2)
                h.save_run(out_dir, variant, r, results)
                completed += 1

    out_base = out_dir.parent.parent
    print("\nDone ({0} arm-runs).".format(completed))
    print("Analyze:")
    print("  python research/score_endstate.py {0} --tool={1} --arms with_session,with_gate".format(
        out_base, args.tool))
    print("  python research/analyze_uncached_cost.py {0} --tool {1}".format(out_base, args.tool))


if __name__ == "__main__":
    main()
