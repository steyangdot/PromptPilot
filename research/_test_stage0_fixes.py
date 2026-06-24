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
def _turn(turn, changed, ledger_ok=True, timed_out=False, censored=False,
          expected_action="modify"):
    return {"turn": turn, "ledger_ok": ledger_ok, "timed_out": timed_out,
            "expected_action": expected_action,
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


def test_classify_explain_final_not_bail():
    # an 'explain' final turn that edits nothing is CORRECT, not a bail (review: expected_action)
    runs = [_turn(1, ["a.py"]), _turn(2, ["b.py"]), _turn(3, [], expected_action="explain")]
    check("explain final no-edit != bail", ct.classify_run(runs)["class"], "clean")


def test_classify_empty_run():
    # an empty run is malformed -> 'unknown', never silently 'clean' (review)
    check("empty run is unknown not clean", ct.classify_run([])["class"], "unknown")


def test_score_orphan_tn_requires_clean():
    # The oracle scorer must NOT credit a crashed/None prediction on a clean case as a true
    # negative (the old `p != 'orphaned'` branch fabricated specificity). (review, _oracle_groundtruth)
    import _oracle_groundtruth as og
    clean_ids = [c["id"] for c in og.ORPHAN_CASES if c["gold"] == "clean"]
    orph_ids = [c["id"] for c in og.ORPHAN_CASES if c["gold"] == "orphaned"]
    truthy("fixture has >=2 clean cases for the crash test", len(clean_ids) >= 2)
    # all orphans caught; one clean predicted correctly; the rest CRASH (None)
    preds = {cid: "orphaned" for cid in orph_ids}
    preds[clean_ids[0]] = "clean"
    for cid in clean_ids[1:]:
        preds[cid] = None
    s = og.score_orphan_predictions(preds)
    check("tn counts only the clean-predicted clean case", s["tn"], 1)
    check("crashed clean preds counted as invalid, not tn", s["invalid"], len(clean_ids) - 1)
    check("no false positives", s["fp"], 0)
    check("recall still perfect (all orphans caught)", s["recall"], 1.0)
    # a verifier that crashes on EVERY clean case must score tn=0 (no fabricated specificity)
    all_crash = {cid: "orphaned" for cid in orph_ids}
    for cid in clean_ids:
        all_crash[cid] = None
    s2 = og.score_orphan_predictions(all_crash)
    check("all-crash clean -> tn=0 (no fabricated specificity)", s2["tn"], 0)
    check("all-crash clean -> invalid==#clean", s2["invalid"], len(clean_ids))


def test_classify_missing_action_still_bails():
    # re-scoring OLD run data (records predating expected_action): a no-edit final turn must
    # STILL flag no_edit_bail — a missing key defaults to edit-expecting (review).
    runs = [{"turn": 1, "ledger_ok": True, "timed_out": False,
             "score": {"changed": ["a.py"], "censored": False}},
            {"turn": 2, "ledger_ok": True, "timed_out": False,
             "score": {"changed": [], "censored": False}}]   # no expected_action key
    check("missing expected_action -> still bails", ct.classify_run(runs)["class"], "no_edit_bail")


def test_classify_edit_vocab_bails():
    # a non-"modify" EDIT action (smoke vocab: add/edit/refactor) with no edits is a bail (review)
    runs = [_turn(1, ["a.py"]), _turn(2, [], expected_action="add")]
    check("'add' final no-edit -> bail", ct.classify_run(runs)["class"], "no_edit_bail")


def test_classify_recovered_timeout_final_not_bail():
    # a RECOVERED timeout on the final turn is a timeout, not an execution bail (review)
    runs = [_turn(1, ["a.py"]),
            {"turn": 2, "ledger_ok": True, "timed_out": False, "recovered_after_timeout": True,
             "expected_action": "modify", "score": {"changed": [], "censored": False}}]
    check("recovered-timeout final != bail", ct.classify_run(runs)["class"], "clean")


def test_guard_framing_survives_many_contracts():
    # code-review #1: the additive-bias directive must NOT be truncated away by GUARD_MAX_CHARS
    # when many contracts are surfaced (the multi-contract refactor regime it exists for). It is
    # emitted FIRST (after the header) precisely so the trailing-line cap can't drop it.
    import tempfile
    import memory_ledger as ml
    d = tempfile.mkdtemp(prefix="s0guard_")
    ml.clear_ledger(d)
    led = ml.load_ledger(d)
    ml.merge_contracts(led, [{"feature": "feat-%02d" % k,
                              "contract": "contract %02d preserves the public kwarg_%02d argument" % (k, k),
                              "tests": ["test_feat_%02d_kwarg" % k]} for k in range(20)], turn=1)
    ml.save_ledger(d, led)
    out = ml.refactor_guard_checklist(d, "refactor everything into one config dataclass", None)
    ml.clear_ledger(d)
    truthy("guard truncates SOMETHING at 20 contracts (cap exercised)", "omitted" in out)
    truthy("additive-bias directive PRESENT even with 20 contracts", "PREFER ADDITIVE" in out)


if __name__ == "__main__":
    for t in (test_pytest_flags, test_oracle_k_broadened, test_classify_clean,
              test_classify_ledger_degraded, test_classify_no_edit_bail,
              test_classify_final_timeout_is_not_bail, test_classify_early_empty_is_not_bail,
              test_classify_degraded_precedence, test_classify_explain_final_not_bail,
              test_classify_empty_run, test_score_orphan_tn_requires_clean,
              test_classify_missing_action_still_bails, test_classify_edit_vocab_bails,
              test_classify_recovered_timeout_final_not_bail,
              test_guard_framing_survives_many_contracts):
        t()
    if _fail:
        print("FAIL ({0} assertion(s)):".format(len(_fail)))
        for m in _fail:
            print("  -", m)
        sys.exit(1)
    print("PASS: _pytest_flags (rc->valid; no-match/hang/error = INVALID not clean), "
          "broadened oracle keywords, classify_run (clean/ledger_degraded/no_edit_bail; "
          "final-only bail; timeout != bail; degraded precedence; explain-final != bail; "
          "empty run = unknown; missing-action still bails; edit-vocab bails; recovered-timeout "
          "!= bail), score_orphan_predictions (crash/None != true-negative).")
