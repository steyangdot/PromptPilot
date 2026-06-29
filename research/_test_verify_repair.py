"""Unit tests for the Stage-2 verify-and-repair core (research/verify_repair.py) +
the run9-class oracle fixture (research/_oracle_groundtruth.py).

No model, no network: probes are executed in subprocesses against tiny temp trees; the run9-class
'kept-but-broken' fixture is the through-line. Run: python -m pytest research/_test_verify_repair.py -q
"""
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # research/ is the worktree copy
import verify_repair as vr            # noqa: E402
import _oracle_groundtruth as og      # noqa: E402

R9_BEFORE, R9_AFTER, R9_PROBE = vr._R9_BEFORE, vr._R9_AFTER, vr._R9_PROBE


def _tree(src: str) -> str:
    d = tempfile.mkdtemp(prefix="vrtest_")
    (Path(d) / "lib.py").write_text(src, encoding="utf-8")
    return d


def test_probe_clean_on_pre_bug_tree():
    d = _tree(R9_BEFORE)
    try:
        assert vr.run_probe(R9_PROBE, d)[0] == "clean"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_probe_violated_on_kept_but_broken():
    """The core claim: an EXECUTING probe catches run9's kept-but-broken tax (TypeError)."""
    d = _tree(R9_AFTER)
    try:
        status, ev = vr.run_probe(R9_PROBE, d)
        assert status == "violated"
        assert ev and "TypeError" in ev["error"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_diff_check_misses_kept_but_broken():
    """The contrast: a removed-symbol diff check does NOT flag run9 (kwarg kept), but the EXECUTION
    oracle DOES. (The destructive case's symbol also survives in ResilienceConfig, so a substring
    check is unreliable for both classes -> only execution is trustworthy.)"""
    assert og.removed_public_symbol(R9_BEFORE, R9_AFTER, "connect_timeout") is False
    r9 = next(c for c in og.ORPHAN_CASES if c["id"] == "kwarg-kept-but-broken")
    assert og.run_orphan_oracle(r9)["verdict"] == "orphaned"


def test_birth_negative_control_rejects_vacuous():
    impl = _tree(R9_BEFORE)
    base = tempfile.mkdtemp(prefix="vrbase_")   # surface present (get exists) but behavior absent
    (Path(base) / "lib.py").write_text("def get(**kw):\n    return {}\n", encoding="utf-8")
    try:
        good_ok, good = vr.validate_probe_at_birth(R9_PROBE, impl, base)
        vac_ok, vac = vr.validate_probe_at_birth(vr._VACUOUS_PROBE, impl, base)
        assert good_ok is True, good
        assert vac_ok is False and vac["reason"] == "vacuous-passes-on-baseline"
    finally:
        shutil.rmtree(impl, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)


def test_unknown_failclosed_malformed_probe():
    d = _tree(R9_AFTER)
    try:
        assert vr.run_probe("from lib import get\nassert get(", d)[0] == "unknown"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unknown_failclosed_import_error():
    d = _tree("x = 1\n")  # no `get` -> ImportError is unverifiable, not a contract violation
    try:
        assert vr.run_probe("from lib import get\nget()\n", d)[0] == "unknown"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sandbox_rejects_unsafe_and_allows_good():
    assert vr.sandbox_check("import os\nos.system('x')\n")[0] is False
    assert vr.sandbox_check("import subprocess\n")[0] is False
    assert vr.sandbox_check("from lib import get\nopen('x','w')\n")[0] is False
    assert vr.sandbox_check(R9_PROBE)[0] is True


def test_tax_accounting_unknown_is_not_clean():
    assert vr.run_tax_status({"checks": [{"status": "violated"}]}) == "taxed"
    assert vr.run_tax_status({"checks": [{"status": "unknown"}, {"status": "clean"}]}) == "unverified"
    assert vr.run_tax_status({"checks": [{"status": "clean"}]}) == "clean"
    assert vr.run_tax_status({"checks": []}) == "none"


def test_verify_contracts_overlap_then_final_runs_all():
    d = _tree(R9_AFTER)
    contracts = {
        "connect-timeout": {"files": ["lib.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE},
        "unrelated": {"files": ["other.py"], "symbols": ["zzz"], "probe": R9_PROBE},
    }
    try:
        res = vr.verify_contracts(contracts, d, ["lib.py"], final=False)
        assert {c["feature"] for c in res["checks"]} == {"connect-timeout"}  # overlap filter
        assert vr.run_tax_status(res) == "taxed"
        res_final = vr.verify_contracts(contracts, d, ["lib.py"], final=True)
        assert {c["feature"] for c in res_final["checks"]} == {"connect-timeout", "unrelated"}
        assert res_final["all_probes_run"] is True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_verify_contracts_no_probe_no_tests_is_unknown():
    d = _tree(R9_AFTER)
    try:
        res = vr.verify_contracts({"x": {"files": ["lib.py"]}}, d, ["lib.py"], final=True)
        assert res["checks"][0]["status"] == "unknown"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_repair_accept_path():
    d = _tree(R9_AFTER)
    try:
        acc = vr.repair_and_reconcile(
            d,
            lambda c: (Path(c) / "lib.py").write_text(R9_BEFORE, encoding="utf-8"),
            lambda c: vr.run_probe(R9_PROBE, c)[0] == "clean")
        assert acc == {"outcome": "repaired", "rolled_back": False}
        assert vr.run_probe(R9_PROBE, d)[0] == "clean"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_repair_rollback_path_restores_pre_repair_tree():
    d = _tree(R9_AFTER)
    try:
        roll = vr.repair_and_reconcile(
            d,
            lambda c: (Path(c) / "lib.py").write_text("def get(**k):\n    raise RuntimeError\n", encoding="utf-8"),
            lambda c: vr.run_probe(R9_PROBE, c)[0] == "clean")
        assert roll == {"outcome": "unrepaired", "rolled_back": True}
        # rolled back to the (still-broken) pre-repair tree, NOT the worse repaired one
        assert vr.run_probe(R9_PROBE, d)[0] == "violated"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_repair_rollback_removes_added_files():
    d = _tree(R9_AFTER)
    try:
        def bad_repair(c):
            (Path(c) / "newfile.py").write_text("x=1\n", encoding="utf-8")  # repair adds a file
        res = vr.repair_and_reconcile(d, bad_repair, lambda c: False)  # always "still violated" -> rollback
        assert res == {"outcome": "unrepaired", "rolled_back": True}    # #10: assert the outcome too
        assert not (Path(d) / "newfile.py").exists()  # added file removed by rollback
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_generate_probe_tolerates_judge_protocol_shapes():
    # PR#49 P1: robust to the project 3-tuple callable, a bare-string callable, AND a .complete object.
    P = "```python\n" + R9_PROBE + "```"
    assert "from lib import get" in vr.generate_probe({"feature": "f", "contract": "c"}, judge=lambda p: (P, 0.0, 0.0))
    assert "from lib import get" in vr.generate_probe({"feature": "f", "contract": "c"}, judge=lambda p: P)

    class Complete:                    # not callable, exposes .complete -> handled by the fallback
        def complete(self, p):
            return P
    assert "from lib import get" in vr.generate_probe({"feature": "f", "contract": "c"}, judge=Complete())


def test_generate_probe_unknown_shape_returns_none_not_crash():
    # PR#49 P1: a truly unknown return shape (int/dict/None/empty) -> None, never a crash
    # (regression for the 'tuple has no attribute strip' bug). None is a RECORDED miss, not a gap.
    for bad in (12345, {"x": 1}, None, ()):
        assert vr.generate_probe({"feature": "f", "contract": "c"}, judge=lambda p, b=bad: b) is None


def test_classify_violation_message_mentioning_importerror_is_violated():
    # #2 regression: a REAL violation whose message contains 'ImportError' must NOT be downgraded to
    # unknown (the old substring scan did exactly that).
    d = _tree("def get(**k):\n    raise AssertionError('expected ImportError handling, got None')\n")
    try:
        status, ev = vr.run_probe("from lib import get\nget()\n", d)
        assert status == "violated", (status, ev)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_classify_nonexception_nonzero_exit_is_unknown():
    # #2 regression: rc!=0 with no python traceback (sys.exit) is NOT a contract violation -> unknown.
    d = _tree("x = 1\n")
    try:
        assert vr.run_probe("raise SystemExit(3)\n", d)[0] == "unknown"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_run_probe_unicode_traceback_no_crash():
    # #4 regression: a non-ASCII traceback must not crash run_probe (utf-8 + errors=replace) and is
    # still classified by the (ASCII) exception TYPE.
    d = _tree("def get(**k):\n    raise ValueError(chr(0x2713) + ' caf' + chr(0xe9))\n")
    try:
        assert vr.run_probe("from lib import get\nget()\n", d)[0] == "violated"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_run_tests_collection_error_is_unknown_not_violated():
    # #3 regression: a pytest collection error (rc 2 -- infra, not a contract failure) -> unknown.
    d = tempfile.mkdtemp(prefix="vrtests_")
    (Path(d) / "test_bad.py").write_text("import nonexistent_module_zzz\n", encoding="utf-8")
    try:
        assert vr._run_tests(["test_bad.py"], d, 30)[0] == "unknown"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sandbox_blocks_getattr_builtins_bypass():
    # #5 regression: the confirmed file-writing bypass + the __class__ escape chain are now rejected.
    assert vr.sandbox_check("g = getattr(__builtins__, 'open')\ng('x', 'w')\n")[0] is False
    assert vr.sandbox_check("().__class__.__bases__[0].__subclasses__()\n")[0] is False
    assert vr.sandbox_check("import os\n")[0] is False
    # PR#49 review: builtins reached via a dunder ATTRIBUTE chain (print.__self__ is the builtins
    # module) must also be blocked by the blanket dunder ban.
    assert vr.sandbox_check("print.__self__.open('x', 'w')\n")[0] is False
    assert vr.sandbox_check("len.__self__.open('x', 'w').write('x')\n")[0] is False


def test_sandbox_no_longer_false_rejects_legit_probes():
    # #5 regression: a variable named `socket`, 'open(' in a string, and a .open() method must PASS.
    assert vr.sandbox_check("socket = 1\nassert socket == 1\n")[0] is True
    assert vr.sandbox_check("s = 'no open( here'\nassert 'open' in s\n")[0] is True
    assert vr.sandbox_check("from lib import get\nr = get()\nr.open()\n")[0] is True


def test_run_probe_bypass_rejected_writes_no_file():
    # #5 end-to-end: the bypass is rejected -> unknown, and NO file is created on disk.
    d = _tree("x = 1\n")
    try:
        status, _ = vr.run_probe("g = getattr(__builtins__, 'open')\ng('PWNED.txt', 'w').write('x')\n", d)
        assert status == "unknown"
        assert not (Path(d) / "PWNED.txt").exists()
        # PR#49 review: the dunder-attribute builtins bridge must also write no file.
        status2, _ = vr.run_probe("print.__self__.open('PWNED2.txt', 'w').write('x')\n", d)
        assert status2 == "unknown"
        assert not (Path(d) / "PWNED2.txt").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_at_risk_fires_on_symbol_overlap_not_just_files():
    # #7: a contract whose FILE set doesn't overlap the changed file but whose SYMBOL appears in the
    # changed file's content is at-risk on a NON-final turn (files-OR-symbols, matching the guard).
    d = _tree(R9_AFTER)   # lib.py mentions connect_timeout
    contracts = {"ct": {"files": ["other.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE}}
    try:
        res = vr.verify_contracts(contracts, d, ["lib.py"], final=False)
        assert {c["feature"] for c in res["checks"]} == {"ct"}   # caught via symbol-in-changed-content
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_restore_tree_handles_readonly_file_and_removes_added():
    # #6: rollback tolerates a read-only file (no crash) and removes repair-added files, without
    # following symlinks.
    d = _tree(R9_AFTER)
    ro = Path(d) / "ro.txt"
    ro.write_text("keep", encoding="utf-8")
    os.chmod(ro, stat.S_IREAD)
    snap = vr._snapshot_tree(d)
    (Path(d) / "added.py").write_text("x = 1\n", encoding="utf-8")  # simulate a repair adding a file
    try:
        vr._restore_tree(d, snap)                        # must not raise on the read-only file
        assert not (Path(d) / "added.py").exists()        # added file removed by rollback
        assert (Path(d) / "ro.txt").exists()              # read-only file preserved
    finally:
        try:
            os.chmod(Path(d) / "ro.txt", stat.S_IWRITE)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(snap, ignore_errors=True)


def test_oracle_run9_case_is_orphaned_by_construction():
    case = next(c for c in og.ORPHAN_CASES if c["id"] == "kwarg-kept-but-broken")
    assert og.run_orphan_oracle(case)["verdict"] == "orphaned"


def test_vr_and_oracle_run9_fixtures_agree():
    # #10 anti-drift: the vr-side _R9_AFTER and the oracle's kwarg-kept-but-broken `change` must
    # break the SAME contract at runtime, so editing one without the other is caught here.
    oracle_change = next(c for c in og.ORPHAN_CASES
                         if c["id"] == "kwarg-kept-but-broken")["change"]["lib.py"]
    d = _tree(oracle_change)
    try:
        assert vr.run_probe(R9_PROBE, d)[0] == "violated"   # oracle's change breaks the vr probe too
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ledger_drops_oversize_probe():
    # PR#49 P2: an over-cap probe payload is DROPPED (sidecar can't bloat); a normal probe is kept.
    import memory_ledger as ml
    huge = "x = 1\n" * ml.PROBE_MAX_CHARS                          # >> the cap
    led = ml.merge_contracts({"version": ml.LEDGER_VERSION, "contracts": {}},
                             [{"feature": "f", "contract": "c", "files": ["lib.py"], "probe": huge}], turn=1)
    assert "probe" not in led["contracts"]["f"]
    small = "from lib import get\nget()\n"
    led2 = ml.merge_contracts({"version": ml.LEDGER_VERSION, "contracts": {}},
                              [{"feature": "f", "contract": "c", "files": ["lib.py"], "probe": small}], turn=1)
    assert led2["contracts"]["f"]["probe"] == small


# --- Harness hook (docs §7): the end-to-end verify-then-gated-repair step run_chain_once invokes,
# exercised here with a STUB repair_runner (no model) -- the live-path integration smoke (P2 gap). ---
def test_verify_and_maybe_repair_detects_and_repairs():
    d = _tree(R9_AFTER)
    contracts = {"ct": {"files": ["lib.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE}}
    def stub_repair(cwd, violations):
        (Path(cwd) / "lib.py").write_text(R9_BEFORE, encoding="utf-8")
    try:
        m = vr.verify_and_maybe_repair(contracts, d, ["lib.py"], final=True, repair_runner=stub_repair)
        assert m["violated"] == 1 and m["repair_fired"] is True
        assert m["repair_outcome"] == "repaired" and m["rolled_back"] is False
        assert vr.run_probe(R9_PROBE, d)[0] == "clean"       # repaired tree accepted
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_verify_and_maybe_repair_rolls_back_failed_repair():
    d = _tree(R9_AFTER)
    contracts = {"ct": {"files": ["lib.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE}}
    def bad_repair(cwd, violations):
        (Path(cwd) / "newfile.py").write_text("x=1\n", encoding="utf-8")   # does not fix it
    try:
        m = vr.verify_and_maybe_repair(contracts, d, ["lib.py"], final=True, repair_runner=bad_repair)
        assert m["repair_fired"] and m["repair_outcome"] == "unrepaired" and m["rolled_back"]
        assert not (Path(d) / "newfile.py").exists()         # rolled back
        assert vr.run_probe(R9_PROBE, d)[0] == "violated"    # pre-repair (broken) tree restored
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_verify_and_maybe_repair_clean_fires_no_repair():
    d = _tree(R9_BEFORE)
    contracts = {"ct": {"files": ["lib.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE}}
    fired = []
    try:
        m = vr.verify_and_maybe_repair(contracts, d, ["lib.py"], final=True,
                                       repair_runner=lambda c, v: fired.append(1))
        assert m["violated"] == 0 and m["repair_fired"] is False and m["status"] == "clean" and fired == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ledger_persists_probe_and_snapshot_detaches():
    import memory_ledger as ml
    d = tempfile.mkdtemp(prefix="vrledger_")
    try:
        led = ml.merge_contracts({"version": ml.LEDGER_VERSION, "contracts": {}},
                                 [{"feature": "connect-timeout", "contract": "per-request override",
                                   "files": ["lib.py"], "symbols": ["connect_timeout"], "probe": R9_PROBE}],
                                 turn=1)
        assert led["contracts"]["connect-timeout"]["probe"] == R9_PROBE  # probe field preserved by merge
        assert ml.save_ledger(d, led) is True
        snap = ml.snapshot_ledger(d)
        assert snap["contracts"]["connect-timeout"]["probe"] == R9_PROBE
        snap["contracts"]["connect-timeout"]["probe"] = "MUTATED"        # mutating snapshot...
        assert ml.snapshot_ledger(d)["contracts"]["connect-timeout"]["probe"] == R9_PROBE  # ...doesn't touch disk
    finally:
        shutil.rmtree(d, ignore_errors=True)
