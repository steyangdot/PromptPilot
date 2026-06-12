"""Claude-code real-bug session-mechanism isolation: WITH_SESSION vs slm_native vs BUILTIN.

Re-runs the claude-code seeded-bug chain to resolve, on END-STATE (not the unfit per-turn diff),
whether prpt's bounded session trades success vs the tool's native resume — the claude analogue
of the codex isolation (memory: codex_realbug_with_vs_builtin.md). Now possible because
capture_end_state (#35) writes gold end-state artifacts and score_endstate --tool=claude-code
scores them; the claude side never had an end-state scorer before.

Arms:
  with_session = SLM rewrite + prpt BOUNDED session
  slm_native   = SLM rewrite + tool NATIVE resume (no bounded session)
  builtin      = RAW prompt   + tool native resume
  => WITH_SESSION vs slm_native = pure bounded-vs-native mechanism (SLM rewrite held constant)
  => slm_native  vs builtin     = the rewrite's effect (native resume held constant)

Pre-registered metric basis: END-STATE success (score_endstate captured artifacts) + UNCACHED
tokens + cache-aware $/success — never the per-turn-diff score (the artifact this re-run exists to
escape). Interleaved A,B,C per run to kill time-of-day / provider-cache-warm confounds; serial;
repo reset per run; resume-aware. SLM = slm-openai (OpenAI key, OFF the Anthropic/claude quota so
the SLM layer never competes with the claude agent).

REQUIRES: C:/projects/httpx @ seeded-timeout-bug (dbeced8); CLAUDE_MODEL=claude-opus-4-8
(the bare "opus" alias resolves to 4.7 — set it explicitly). Real opus quota -> off-peak.
Resume-aware: a completed run is loaded, so a quota window that fits only a few runs still
progresses. After it finishes:
  python research/score_endstate.py <OUT_BASE> --tool=claude-code --arms with_session,slm_native,builtin
  python research/analyze_uncached_cost.py <OUT_BASE> --tool claude-code

    set PROMPTPILOT_OUT_DIR=B:\LLM\_session_retest_2026-06-07\claude_isolation
    set CLAUDE_MODEL=claude-opus-4-8
    python research/session_isolation_experiment.py --runs 5
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
from prpt._subprocess import claude_subprocess_session  # noqa: E402

ARMS = ["with_session", "slm_native", "builtin"]
NATIVE = {"slm_native", "builtin"}  # these use the tool's native resume (USE_BUILTIN_SESSION=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="Runs per arm (default 5)")
    ap.add_argument("--chain", default="chain1")
    ap.add_argument("--tool", default="claude-code", choices=["claude-code", "codex"])
    ap.add_argument("--normalizer", default="slm-openai",
                    help="SLM layer (default slm-openai = OpenAI key, off the claude quota)")
    args = ap.parse_args()

    h._NORMALIZER_NAME = args.normalizer
    chain = next((c for c in h.CHAINS if c["id"] == args.chain), None)
    if chain is None:
        raise SystemExit("unknown chain: {0}".format(args.chain))
    out_dir = h.OUT_DIR / args.tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    model = os.environ.get("CLAUDE_MODEL")
    print("=" * 80)
    print("session isolation  arms={0}  runs={1}  chain={2}  tool={3}  normalizer={4}".format(
        ARMS, args.runs, chain["id"], args.tool, args.normalizer))
    print("CLAUDE_MODEL:", model or "(unset -> 'opus' alias = 4.7! set CLAUDE_MODEL=claude-opus-4-8)")
    print("out_dir:", out_dir)
    print("fixture: C:/projects/httpx @ seeded-timeout-bug (verify HEAD = dbeced8 before running)")
    print("=" * 80)

    done = 0
    failed = []
    with claude_subprocess_session("session_isolation"):
        for r in range(1, args.runs + 1):
            for variant in ARMS:                       # interleaved A,B,C per run index
                if h.load_run(out_dir, variant, r) is not None:
                    print("  [resume] {0} run {1} already complete -> load".format(variant, r))
                    done += 1
                    continue
                if variant in NATIVE:
                    os.environ["USE_BUILTIN_SESSION"] = "1"
                else:
                    os.environ.pop("USE_BUILTIN_SESSION", None)
                print("\n--- {0} run {1}/{2} ---".format(variant, r, args.runs))
                try:
                    results = h.run_chain_once(chain, args.tool, variant, r, out_dir)
                except h.QuotaExhausted as e:
                    print("\n[abort] {0}\nResume after the quota window resets — runs are kept.".format(e))
                    raise SystemExit(2)
                except Exception:
                    # L2 (2026-06-11): one bad arm-run must NOT kill the experiment — log
                    # the traceback (visible now that logging is unbuffered) and move on.
                    # The run isn't saved, so a supervisor restart / next invocation retries it.
                    import traceback
                    print("\n[arm-run FAILED] {0} run {1} — continuing:".format(variant, r))
                    traceback.print_exc(file=sys.stdout)
                    failed.append((variant, r))
                    continue
                finally:
                    os.environ.pop("USE_BUILTIN_SESSION", None)
                h.save_run(out_dir, variant, r, results)
                done += 1

    ob = out_dir.parent.parent
    print("\nDone ({0} arm-runs complete, {1} failed{2}).".format(
        done, len(failed), ": " + str(failed) if failed else ""))
    print("  python research/score_endstate.py {0} --tool={1} "
          "--arms with_session,slm_native,builtin".format(ob, args.tool))
    print("  python research/analyze_uncached_cost.py {0} --tool {1}".format(ob, args.tool))
    # exit 0 only when everything is saved -> the supervisor uses this to decide on a relaunch
    raise SystemExit(0 if done >= args.runs * len(ARMS) else 3)


if __name__ == "__main__":
    main()
