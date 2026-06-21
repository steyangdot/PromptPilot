"""Stage-0 measurement-validity self-test.

Covers the testable logic of the Stage-0 fixes:
  - _pytest_flags(rc): only rc 0/1 are VALID; rc 5 (no match) / 124 (hang) / 125
    (error) are INVALID, not clean (review P2a).
  - _ORACLE_K: the broadened oracle keyword set covers the non-timeout orphan class.
  - classify_run(): clean / ledger_degraded / no_edit_bail (review P1), final-turn-only
    bail signal, timeout != bail.

Pure stdlib (no pytest); exits non-zero on failure.
Run:  python research/_test_stage0_fixes.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import chain_test_v2 as ct  # noqa: E402

_fail = []


def check(name, got, want):
    if got != want:
        _fail.append("{0}: got {1!r}, want {2!r}".format(name, got, want))


def truthy(name, cond):
    if not cond:
        _fail.append("{0}: expected truthy".format(name))


# --- _pytest_flags: only rc 0/1 are valid; 5/124/125 = INVALID, not clean ----
def test_pytest_flags():
    check("rc=0 passed", ct._pytest_flags(0)["pytest_passed"], True)
    check("rc=0 valid", ct._pytest_flags(0)["pytest_valid"], True)
    check("rc=1 not passed", ct._pytest_flags(1)["pytest_passed"], False)
    check("rc=1 still valid (real failures ran)", ct._pytest_flags(1)["pytest_valid"], True)
    check("rc=5 no_match", ct._pytest_flags(5)["pytest_no_match"], True)
    check("rc=5 INVALID (not clean)", ct._pytest_flags(5)["pytest_valid"], False)
    check("rc=5 not counted as passed", ct._pytest_flags(5)["pytest_passed"], False)
    check("rc=124 (hang) invalid", ct._pytest_flags(124)["pytest_valid"], False)
    check("rc=125 (error) invalid", ct._pytest_flags(125)["pytest_valid"], False)


def test_oracle_k_broadened():
    truthy("oracle covers 'pool' (the missed orphan class)", "pool" in ct._ORACLE_K)
    truthy("oracle still covers 'timeout' (seeded-bug smoke)", "timeout" in ct._ORACLE_K)


# --- classify_run ------------------------------------------------------------
def _turn(turn, changed, ledger_ok=True, timed_out=False, censored=False):
    return {"turn": turn, "ledger_ok": ledger_ok, "timed_out": timed_out,
            "score": {"changed": changed, "censored": censored}}


def test_classify_clean():
    runs = [_turn(1, ["a.py"]), _turn(2, ["b.py"]), _turn(3, ["c.py"])]
    check("clean run", ct.classify_run(runs)["class"], "clean")


def test_classify_ledger_degraded():
    runs = [_turn(1, ["a.py"]), _turn(2, [], ledger_ok=False), _turn(3, ["c.py"])]
    c = ct.classify_run(runs)
    check("ledger_degraded class", c["class"], "ledger_degraded")
    check("flagged the failed turn", c["ledger_failed_turns"], [2])


def test_classify_no_edit_bail():
    runs = [_turn(1, ["a.py"]), _turn(2, ["b.py"]), _turn(3, [])]   # final produced no edits
    c = ct.classify_run(runs)
    check("no_edit_bail class", c["class"], "no_edit_bail")
    check("final_no_edit flag", c["final_no_edit"], True)


def test_classify_final_timeout_is_not_bail():
    # a TIMED-OUT final turn with no recorded edits is censored/excluded, NOT a bail
    runs = [_turn(1, ["a.py"]), _turn(2, ["b.py"]), _turn(3, [], timed_out=True, censored=True)]
    check("timed-out final != bail", ct.classify_run(runs)["class"], "clean")


def test_classify_early_empty_is_not_bail():
    # an early changed==[] turn (recovered by a later turn) must NOT flag the run
    runs = [_turn(1, []), _turn(2, ["b.py"]), _turn(3, ["c.py"])]
    check("early empty turn != bail", ct.classify_run(runs)["class"], "clean")


def test_classify_degraded_precedence():
    # both signals present -> ledger_degraded is reported as the primary class
    runs = [_turn(1, ["a.py"], ledger_ok=False), _turn(2, ["b.py"]), _turn(3, [])]
    check("degraded takes precedence over bail", ct.classify_run(runs)["class"], "ledger_degraded")


if __name__ == "__main__":
    for t in (test_pytest_flags, test_oracle_k_broadened, test_classify_clean,
              test_classify_ledger_degraded, test_classify_no_edit_bail,
              test_classify_final_timeout_is_not_bail, test_classify_early_empty_is_not_bail,
              test_classify_degraded_precedence):
        t()
    if _fail:
        print("FAIL ({0} assertion(s)):".format(len(_fail)))
        for m in _fail:
            print("  -", m)
        sys.exit(1)
    print("PASS: _pytest_flags (rc->valid; no-match/hang/error = INVALID not clean), "
          "broadened oracle keywords, classify_run (clean/ledger_degraded/no_edit_bail; "
          "final-only bail; timeout != bail; degraded precedence).")
