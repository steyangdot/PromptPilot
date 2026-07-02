"""Tests for the thread-cumulative usage correction (2026-07-01 re-audit fix).

Covers delta_cumulative_usage (first-turn / cumulative-delta / non-monotone fallback) and
rebuild_native_delta_chain (plain chain, censored-gap absorption, post-recovery
redistribution, old-artifact passthrough), plus an ARTIFACT-GROUNDED check: rebuilding the
real chain_long builtin_run1.json must make sum(per-turn deltas) == last-turn cumulative.

Standalone: python research/_test_cumulative_usage.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain_test_v2 import delta_cumulative_usage, rebuild_native_delta_chain  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print("  [{0}] {1}: got {2!r}{3}".format("ok" if ok else "FAIL", name, got,
                                             "" if ok else " want {0!r}".format(want)))
    if not ok:
        FAILS.append(name)


def u(gin, cached, out, tc=1):
    return {"input_tokens": gin, "cached_tokens": cached,
            "uncached_tokens": gin - cached, "output_tokens": out, "tool_calls": tc}


def test_first_turn_passthrough():
    usage, sem = delta_cumulative_usage(u(100, 40, 10), None)
    check("first turn: unchanged input", usage["input_tokens"], 100)
    check("first turn: semantics", sem, "thread_cumulative_first")


def test_cumulative_delta():
    prev = u(100, 40, 10)
    usage, sem = delta_cumulative_usage(u(250, 130, 25, tc=7), prev)
    check("delta input", usage["input_tokens"], 150)
    check("delta cached", usage["cached_tokens"], 90)
    check("delta uncached", usage["uncached_tokens"], 60)
    check("delta output", usage["output_tokens"], 15)
    check("tool_calls pass through (per-invocation)", usage["tool_calls"], 7)
    check("semantics", sem, "thread_cumulative_delta")


def test_non_monotone_fallback():
    # old codex (pre ~06-10) reported per-invocation usage: a later turn can be SMALLER.
    prev = u(500, 200, 50)
    usage, sem = delta_cumulative_usage(u(300, 120, 30), prev)
    check("non-monotone: raw kept", usage["input_tokens"], 300)
    check("non-monotone: semantics", sem, "per_invocation_non_monotone")


def _rec(turn, raw):
    return {"turn": turn, "usage": {"input_tokens": 0}, "usage_cumulative_raw": raw,
            "uncached_input": 0}


def test_rebuild_plain_chain():
    recs = [_rec(1, u(100, 40, 10)), _rec(2, u(250, 130, 25)), _rec(3, u(400, 250, 40))]
    n = rebuild_native_delta_chain(recs)
    check("rebuild: all changed", n, 3)
    check("t1 delta", recs[0]["usage"]["input_tokens"], 100)
    check("t2 delta", recs[1]["usage"]["input_tokens"], 150)
    check("t3 delta", recs[2]["usage"]["input_tokens"], 150)
    check("t3 uncached_input recomputed", recs[2]["uncached_input"], 150 - 120)


def test_censored_gap_then_recovery():
    # t2 censored (no raw): t3's delta must span t1->t3 (absorbs the gap; totals correct).
    recs = [_rec(1, u(100, 40, 10)), _rec(2, None), _rec(3, u(400, 250, 40))]
    rebuild_native_delta_chain(recs)
    check("gap: t3 absorbs killed t2", recs[2]["usage"]["input_tokens"], 300)
    check("gap: t2 untouched (still censored)", recs[1]["usage"]["input_tokens"], 0)
    # reparse later recovers t2's flushed raw -> rebuild redistributes t2/t3.
    recs[1]["usage_cumulative_raw"] = u(250, 130, 25)
    rebuild_native_delta_chain(recs)
    check("recovered: t2 delta", recs[1]["usage"]["input_tokens"], 150)
    check("recovered: t3 delta shrinks", recs[2]["usage"]["input_tokens"], 150)


def test_old_artifacts_untouched():
    recs = [{"turn": 1, "usage": {"input_tokens": 123}},
            {"turn": 2, "usage": {"input_tokens": 456}}]
    n = rebuild_native_delta_chain(recs)
    check("no raw field: nothing changed", n, 0)
    check("no raw field: usage intact", recs[1]["usage"]["input_tokens"], 456)


def test_artifact_grounded_chain_long():
    """The real builtin_run1.json (recorded naively = cumulative per-turn): rebuilding it
    must yield sum(deltas) == last-turn cumulative (the audit's corrected total)."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                     "chain_results_v2", "codex", "chain_long", "builtin_run1.json")
    if not os.path.exists(p):
        print("  [skip] artifact not present: {0}".format(p))
        return
    records = json.load(open(p, encoding="utf-8"))
    recs = [dict(r) for r in records]         # do NOT mutate the artifact on disk
    for r in recs:
        r["usage_cumulative_raw"] = dict(r["usage"])   # recorded value WAS the cumulative
    rebuild_native_delta_chain(recs)
    total = sum(r["usage"]["input_tokens"] for r in recs)
    last_cum = records[-1]["usage"]["input_tokens"]
    check("artifact: sum(deltas) == last-turn cumulative (gross)", total, last_cum)
    tot_unc = sum(r["usage"].get("uncached_tokens", 0) for r in recs)
    last_unc = records[-1]["usage"].get("uncached_tokens",
                                        records[-1]["usage"]["input_tokens"]
                                        - records[-1]["usage"].get("cached_tokens", 0))
    check("artifact: sum(deltas) == last-turn cumulative (uncached)", tot_unc, last_unc)
    mono = all(records[i]["usage"]["input_tokens"] <= records[i + 1]["usage"]["input_tokens"]
               for i in range(len(records) - 1))
    check("artifact: raw series is monotone (cumulative signature)", mono, True)


if __name__ == "__main__":
    for fn in (test_first_turn_passthrough, test_cumulative_delta, test_non_monotone_fallback,
               test_rebuild_plain_chain, test_censored_gap_then_recovery,
               test_old_artifacts_untouched, test_artifact_grounded_chain_long):
        print(fn.__name__)
        fn()
    print()
    if FAILS:
        print("FAILED: {0}".format(FAILS)); sys.exit(1)
    print("ALL PASS")
