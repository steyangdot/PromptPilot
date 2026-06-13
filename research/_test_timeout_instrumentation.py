"""
Self-test: timed-out turns must be EXCLUDED-and-COUNTED, never counted as real
0-token / 0-cost successes.

Why this exists: a turn that hit the subprocess wall-clock cap records 0 tokens
and $0 (claude overwrites its output with '{}'; codex parses before the killed
grandchild flushes its turn.completed) while its partial file edits still earn
file-hash success. Counting such a turn inflated the published uncached/$ ratios
(codex 4.47x -> true ~2.36x) and made claude native look ~2.6x cheaper instead of
~1.8-1.9x. This pins:
  - the shared `turn_timed_out` classifier (incl. the regression that raising the
    claude cap to 2400 must NOT un-flag the known frozen 1800-cap timeouts), and
  - that aggregate_runs / analyze_uncached_cost exclude censored turns and bucket
    recovered (non-zero-usage) timeouts separately from clean turns.

Run:  python research/_test_timeout_instrumentation.py
(pure stdlib, no pytest required; exits non-zero on failure)
"""
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                       # research/
sys.path.insert(0, os.path.dirname(_HERE))      # repo root (for the `prpt` package)

from agentic_variety_test import turn_timed_out  # noqa: E402
from chain_test_v2 import aggregate_runs          # noqa: E402
import analyze_uncached_cost                       # noqa: E402

_failures = []


def check(name, got, want):
    if got != want:
        _failures.append(f"{name}: got {got!r}, want {want!r}")


def _turn(**kw):
    """Minimal per-turn record. Defaults to a clean completed turn."""
    t = {
        "turn": kw.get("turn", 1),
        "raw": "x",
        "expected_action": "modify",
        "had_history": False,
        "wall_t": kw.get("wall_t", 100.0),
        "usage": {
            "input_tokens": kw.get("input_tokens", 50000),
            "cached_tokens": kw.get("cached_tokens", 0),
            "uncached_tokens": kw.get("uncached_tokens",
                                      kw.get("input_tokens", 50000) - kw.get("cached_tokens", 0)),
            "output_tokens": 1000,
            "tool_calls": 5,
        },
        "uncached_input": kw.get("uncached_input",
                                 kw.get("input_tokens", 50000) - kw.get("cached_tokens", 0)),
        "score": {"success": kw.get("success", 1.0), "bailed": False, "changed": ["f"]},
        "total_cost": kw.get("total_cost", 0.1),
        "slm_cost": 0.0,
        "downstream_cost": kw.get("total_cost", 0.1),
    }
    for k in ("rc", "timed_out", "timeout_cap_sec"):
        if k in kw:
            t[k] = kw[k]
    return t


def test_classifier():
    # (a) explicit timed_out True
    check("explicit timed_out", turn_timed_out(_turn(timed_out=True), "claude-code"), True)
    # (b) rc == 124
    check("rc 124", turn_timed_out(_turn(rc=124), "codex"), True)
    # (c) new-format timed_out False with a high wall -> explicit beats heuristic
    check("explicit not-timed-out wins",
          turn_timed_out(_turn(timed_out=False, wall_t=2000.0, input_tokens=0), "claude-code"),
          False)
    # (d) old-format: per-record cap present, wall pegged, zero usage
    check("old cap-present peg",
          turn_timed_out(_turn(timeout_cap_sec=1800, wall_t=1800.4, input_tokens=0), "claude-code"),
          True)
    # (e) old-format completed (slow but real tokens) -> not timed out
    check("slow completed", turn_timed_out(_turn(wall_t=160.0, input_tokens=629951), "claude-code"), False)
    # (f) old-format fast genuine-zero (real bail, not a timeout) -> not timed out
    check("fast genuine zero", turn_timed_out(_turn(wall_t=24.0, input_tokens=0), "claude-code"), False)
    # (g) REGRESSION: old file, NO cap field, wall ~1800, zero usage. With the
    # live CLAUDE_TIMEOUT_SEC now 2400, a 0.97*live-cap heuristic (=2328) would
    # FAIL this and silently revert the known timeout to a free-success turn.
    # The absolute floor (1700) must still flag it.
    check("g: absolute floor flags 1800-peg under 2400 default",
          turn_timed_out(_turn(wall_t=1800.3, input_tokens=0), "claude-code"), True)
    # codex floor 295 catches the 300 peg; a 250s zero-usage turn does not
    check("codex 300 peg", turn_timed_out(_turn(wall_t=300.0, input_tokens=0), "codex"), True)
    check("codex sub-floor zero", turn_timed_out(_turn(wall_t=250.0, input_tokens=0), "codex"), False)


def test_aggregate_excludes_censored():
    # T1 timed out in 3 of 5 runs (cap pegged, zero usage, but success 0.5);
    # 2 runs completed cleanly with real tokens.
    clean = lambda: _turn(turn=1, timed_out=False, wall_t=120.0, input_tokens=40000, success=1.0, total_cost=0.2)
    to = lambda: _turn(turn=1, timed_out=True, wall_t=1800.4, input_tokens=0, success=0.5, total_cost=0.0)
    runs = [[clean()], [clean()], [to()], [to()], [to()]]
    agg = aggregate_runs(runs, "claude-code")[0]
    check("n_runs excludes censored", agg["n_runs"], 2)
    check("timed_out_count", agg["timed_out_count"], 3)
    check("all_timed_out false", agg["all_timed_out"], False)
    check("success mean over clean only", agg["success_mean"], 1.0)
    check("uncached mean over clean only", agg["uncached_input_mean"], 40000)

    # Every run timed out -> sentinel, no statistics.mean([]) crash.
    allto = [[to()] for _ in range(5)]
    agg2 = aggregate_runs(allto, "claude-code")[0]
    check("all-censored n_runs", agg2["n_runs"], 0)
    check("all-censored flag", agg2["all_timed_out"], True)
    check("all-censored success 0", agg2["success_mean"], 0.0)
    check("all-censored count", agg2["timed_out_count"], 5)


def test_analyzer_excludes_and_buckets_recovery():
    # Build a synthetic arm dir: 1 clean turn + 1 lost timeout + 1 RECOVERED
    # codex-style timeout (timed out but carries non-zero recovered usage).
    with tempfile.TemporaryDirectory() as d:
        clean = _turn(turn=1, timed_out=False, wall_t=120.0, input_tokens=40000,
                      cached_tokens=0, success=1.0, total_cost=0.2)
        lost = _turn(turn=2, timed_out=True, wall_t=300.0, input_tokens=0,
                     success=0.5, total_cost=0.0)
        recovered = _turn(turn=3, timed_out=True, wall_t=300.0, input_tokens=134040,
                          cached_tokens=0, uncached_tokens=134040, success=0.5, total_cost=0.0)
        path = os.path.join(d, "with_session_run1.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([clean, lost, recovered], f)
        from pathlib import Path
        st = analyze_uncached_cost._arm_stats(Path(d), "with_session", "codex")
    check("analyzer success excludes both timeouts", st["success"], 1.0)
    check("analyzer uncached excludes both timeouts", st["uncached"], 40000)
    check("analyzer timed_out counts only the lost one", st["timed_out"], 1)
    check("analyzer recovered counts the recovered one", st["recovered"], 1)
    check("analyzer recovered uncached bucketed separately", st["recovered_uncached"], 134040)


if __name__ == "__main__":
    for t in (test_classifier, test_aggregate_excludes_censored,
              test_analyzer_excludes_and_buckets_recovery):
        t()
    if _failures:
        print("FAIL ({} assertion(s)):".format(len(_failures)))
        for msg in _failures:
            print("  -", msg)
        sys.exit(1)
    print("PASS: timed-out turns are excluded-and-counted; absolute-floor "
          "back-compat holds under the raised cap; recovered timeouts bucket "
          "separately from clean turns.")
