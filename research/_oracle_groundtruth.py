"""Precondition #1 (docs/SESSION_MEMORY_RELEVANCE_RISKS.md): the NON-CIRCULAR ground-truth
layer that MUST exist before any retriever/verifier is built or trusted.

Why this file exists
--------------------
The risk register's #1 finding: the relevance design can't be validated on chain_long because
its only non-circular oracle is structurally disabled there (`-k timeout` -> rc=5, the fixture
forbids test execution, the continuity judge is same-tier nano, score_endstate ceilings at 1.000).
Every "accuracy" number would then just ratify a coherent fabrication. This module supplies the
missing ground truth:

  1. LABELED CASES whose label is true BY CONSTRUCTION (no SLM involved):
     - orphan cases: a tiny self-contained project + a "migration" change + gold {orphaned|clean},
       where the gold is *enforced by a real pytest* the change either breaks (orphaned) or keeps
       green (clean). Spans the classes the mechanical guard is blind to — code-kwarg, DATA-semantic
       (units), and INVARIANT (int-cents) — to prove the oracle catches them WHEN the fixture writes
       the asserting test (the whole point: a test-writing fixture gives the oracle teeth).
     - referent cases: a ledger state + a referential prompt + gold referent (or ABSTAIN / NONE).
  2. run_orphan_oracle(case): apply the change to a temp copy, run pytest -> verdict. NON-CIRCULAR
     w.r.t. the SLM (pytest is the truth; no model in the loop).
  3. POSITIVE CONTROLS: an obvious orphan that MUST be detected, an obvious clean that MUST pass, an
     obvious referent that MUST resolve, an obvious-ambiguous that MUST abstain — so we know the
     instrument can detect ANY signal before a parity verdict is ever trusted.
  4. Scorers: score a candidate verifier/retriever's predictions against the gold labels.

Self-test (__main__): runs the oracle over every orphan case and asserts verdict == gold (validates
the ORACLE itself on independently-true labels) + checks the positive controls.

Run:  python research/_oracle_groundtruth.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Orphan cases: {id, subclass, gold, before:{path:src}, change:{path:src}}
# `before` is written + must pass pytest; `change` overwrites the listed files;
# orphaned  <=>  a test that PASSED before now FAILS.
# ---------------------------------------------------------------------------
ORPHAN_CASES = [
    {
        "id": "kwarg-removed-destructive",
        "subclass": "code-contract", "gold": "orphaned", "positive_control": True,
        "note": "T13-style: migration removes the public connect_timeout kwarg the test calls.",
        "before": {
            "lib.py": (
                "def make_client(timeout=None, connect_timeout=None):\n"
                "    return {'timeout': timeout, 'connect_timeout': connect_timeout}\n"
            ),
            "test_lib.py": (
                "from lib import make_client\n"
                "def test_connect_timeout_kwarg():\n"
                "    assert make_client(connect_timeout=1.0)['connect_timeout'] == 1.0\n"
            ),
        },
        "change": {
            "lib.py": (
                "class ResilienceConfig:\n"
                "    def __init__(self, connect_timeout=None):\n"
                "        self.connect_timeout = connect_timeout\n"
                "def make_client(timeout=None, resilience=None):\n"   # connect_timeout REMOVED
                "    return {'timeout': timeout, 'resilience': resilience}\n"
            ),
        },
    },
    {
        "id": "kwarg-additive-clean",
        "subclass": "code-contract", "gold": "clean", "positive_control": True,
        "note": "Additive migration: adds ResilienceConfig but KEEPS the connect_timeout kwarg.",
        "before": {
            "lib.py": (
                "def make_client(timeout=None, connect_timeout=None):\n"
                "    return {'timeout': timeout, 'connect_timeout': connect_timeout}\n"
            ),
            "test_lib.py": (
                "from lib import make_client\n"
                "def test_connect_timeout_kwarg():\n"
                "    assert make_client(connect_timeout=1.0)['connect_timeout'] == 1.0\n"
            ),
        },
        "change": {
            "lib.py": (
                "class ResilienceConfig:\n"
                "    def __init__(self, connect_timeout=None):\n"
                "        self.connect_timeout = connect_timeout\n"
                "def make_client(timeout=None, connect_timeout=None, resilience=None):\n"
                "    if resilience is not None and connect_timeout is None:\n"
                "        connect_timeout = resilience.connect_timeout\n"
                "    return {'timeout': timeout, 'connect_timeout': connect_timeout, 'resilience': resilience}\n"
            ),
        },
    },
    {
        "id": "kwarg-kept-but-broken",
        "subclass": "additive-but-buggy", "gold": "orphaned", "positive_control": True,
        "note": "run9-class (N=10 finding): the public connect_timeout kwarg is KEPT in the signature "
                "(ZERO removed lines) but a merge bug breaks it AT RUNTIME -> only an EXECUTING test/probe "
                "catches it; a diff/removed-symbol check MISSES it (see removed_public_symbol()). Mirrors "
                "httpx _merge_timeout_extensions dict(**ext, timeout=...) duplicate-kwarg TypeError.",
        "before": {
            "lib.py": (
                "def _merge(extensions, connect_timeout):\n"
                "    base = dict(extensions)\n"
                "    if connect_timeout is not None:\n"
                "        base['timeout'] = {'connect': connect_timeout}\n"
                "    return base\n"
                "def get(connect_timeout=None, extensions=None):\n"
                "    return _merge(extensions or {'timeout': {'connect': 5.0}}, connect_timeout)\n"
            ),
            "test_lib.py": (
                "from lib import get\n"
                "def test_connect_timeout_override():\n"
                "    # exercises the RUNTIME path, not just the signature\n"
                "    assert get(connect_timeout=0.5)['timeout']['connect'] == 0.5\n"
            ),
        },
        "change": {
            "lib.py": (
                "def _merge(extensions, connect_timeout):\n"
                "    timeout_extensions = {'connect': connect_timeout} if connect_timeout is not None else None\n"
                "    if timeout_extensions is not None:\n"
                "        # run9 bug: extensions already carries 'timeout' AND timeout=... -> duplicate kwarg\n"
                "        return dict(**extensions, timeout=timeout_extensions)\n"
                "    return dict(extensions)\n"
                "def get(connect_timeout=None, extensions=None):\n"      # signature KEPT (kwarg still accepted)
                "    return _merge(extensions or {'timeout': {'connect': 5.0}}, connect_timeout)\n"
            ),
        },
    },
    {
        "id": "data-semantic-drift-orphaned",
        "subclass": "data-semantic", "gold": "orphaned",
        "note": "Anchorless: a test pins the ms->s conversion contract; the 'standardize' change breaks it.",
        "before": {
            "lib.py": (
                "def to_seconds(stored_value):\n"
                "    # contract: stored values are MILLISECONDS\n"
                "    return stored_value / 1000.0\n"
            ),
            "test_lib.py": (
                "from lib import to_seconds\n"
                "def test_ms_contract():\n"
                "    assert to_seconds(3000) == 3.0\n"
            ),
        },
        "change": {
            "lib.py": (
                "def to_seconds(stored_value):\n"
                "    # drifted to seconds: no longer divides -> historical ms values misread\n"
                "    return float(stored_value)\n"
            ),
        },
    },
    {
        "id": "data-semantic-additive-clean",
        "subclass": "data-semantic", "gold": "clean",
        "note": "Adds a seconds path while preserving the ms contract + its test.",
        "before": {
            "lib.py": (
                "def to_seconds(stored_value):\n"
                "    return stored_value / 1000.0\n"
            ),
            "test_lib.py": (
                "from lib import to_seconds\n"
                "def test_ms_contract():\n"
                "    assert to_seconds(3000) == 3.0\n"
            ),
        },
        "change": {
            "lib.py": (
                "def to_seconds(stored_value):\n"
                "    return stored_value / 1000.0\n"            # ms contract preserved
                "def to_seconds_from_s(stored_value):\n"
                "    return float(stored_value)\n"              # additive new path
            ),
        },
    },
    {
        "id": "invariant-violation-orphaned",
        "subclass": "invariant", "gold": "orphaned",
        "note": "Anchorless: a property test pins 'money stays integer cents'; the change does float math.",
        "before": {
            "lib.py": (
                "def total_with_tax(cents, tax_rate_bps):\n"
                "    # invariant: money is integer minor units (cents)\n"
                "    return cents + (cents * tax_rate_bps) // 10000\n"
            ),
            "test_lib.py": (
                "from lib import total_with_tax\n"
                "def test_total_is_int_cents():\n"
                "    r = total_with_tax(10000, 825)\n"
                "    assert isinstance(r, int) and r == 10825\n"
            ),
        },
        "change": {
            "lib.py": (
                "def total_with_tax(cents, tax_rate_bps):\n"
                "    return cents + cents * (tax_rate_bps / 10000)\n"   # float -> 10825.0, violates int invariant
            ),
        },
    },
]

# ---------------------------------------------------------------------------
# Referent cases: graded against a GOLD label (hand-set; no pytest oracle exists
# for reference resolution — the label IS the ground truth). ABSTAIN is a correct
# answer for genuinely-ambiguous prompts; NONE for unrelated prompts.
# ---------------------------------------------------------------------------
REFERENT_CASES = [
    {
        "id": "ref-clear-pronoun", "gold": "timeout-overrides",
        "ledger": ["timeout-overrides", "retry-after"], "last_active": "timeout-overrides",
        "prompt": "write unit tests for it",
        "note": "Single clear referent: 'it' after timeout-overrides was last active.",
    },
    {
        "id": "ref-named-feature", "gold": "timeout-overrides", "positive_control": True,
        "ledger": ["timeout-overrides", "retry-after"], "last_active": "retry-after",
        "prompt": "add tests for the connect_timeout feature",
        "note": "Names the feature explicitly -> any retriever MUST resolve it (control).",
    },
    {
        "id": "ref-ambiguous-abstain", "gold": "ABSTAIN", "positive_control": True,
        "ledger": ["feat-a", "feat-b", "feat-c"], "last_active": None,
        "prompt": "fix it",
        "note": "3 equally-plausible referents, no anchor -> the ONLY correct answer is ABSTAIN, not a guess.",
    },
    {
        "id": "ref-unrelated-none", "gold": "NONE",
        "ledger": ["timeout-overrides", "retry-after"], "last_active": "retry-after",
        "prompt": "add a brand-new structured logging module",
        "note": "Unrelated request -> no referent should be injected.",
    },
]


# ---------------------------------------------------------------------------
# The non-circular oracle
# ---------------------------------------------------------------------------
def _pytest(cwd: Path, timeout_s: int = 60) -> bool:
    """Run the case's pytest; return True iff it PASSED (rc==0), False if it RAN and FAILED
    (rc != 0). Does NOT swallow exceptions: a bounded-timeout (TimeoutExpired) or infra
    failure (python/pytest missing, OSError) PROPAGATES so run_orphan_oracle records the case
    as 'error' instead of fabricating an 'orphaned' — a swallowed False on the AFTER run reads
    as a fabricated orphan, and on these sub-second by-construction cases a 60s timeout means
    the ENVIRONMENT is wrong, not that the change broke the test."""
    p = subprocess.run([sys.executable, "-m", "pytest", "test_lib.py", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)
    return p.returncode == 0


def run_orphan_oracle(case: dict) -> dict:
    """Apply the case's change to a temp copy and decide orphaned/clean via pytest.
    NON-CIRCULAR: no model involved; the test is the judge. A well-formed case must
    pass BEFORE the change (the contract is established); orphaned <=> it fails AFTER."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for path, src in case["before"].items():
            (root / path).write_text(src, encoding="utf-8")
        try:
            before_passed = _pytest(root)
            for path, src in case["change"].items():        # apply the migration
                (root / path).write_text(src, encoding="utf-8")
            after_passed = _pytest(root)
        except (subprocess.SubprocessError, OSError) as e:
            # A timeout or infra failure is NOT a test result — surface it as 'error' rather
            # than let a broken/slow environment masquerade as an 'orphaned' (false ground
            # truth). Programming errors (KeyError, malformed case dict, ...) are deliberately
            # NOT caught — they crash loudly so a bug can't hide behind a benign 'error'.
            return {"id": case["id"], "before_passed": None, "after_passed": None,
                    "verdict": "error", "error": str(e)}
    verdict = "orphaned" if (before_passed and not after_passed) else \
              ("clean" if (before_passed and after_passed) else "malformed")
    return {"id": case["id"], "before_passed": before_passed,
            "after_passed": after_passed, "verdict": verdict}


def removed_public_symbol(before_src: str, after_src: str, symbol: str) -> bool:
    """The DIFF/SUBSTRING proxy a non-executing verifier would use: did `symbol` (a public
    kwarg/name) disappear from the source? True iff present before and absent after. This is
    exactly the check that MISSES the run9-class 'kept-but-broken' tax (the symbol is retained;
    only runtime behavior breaks) -> demonstrates why execution-based verification is required
    (docs/SESSION_MEMORY_VERIFY_REPAIR.md driving decision)."""
    return (symbol in before_src) and (symbol not in after_src)


# ---------------------------------------------------------------------------
# Scorers — grade a candidate verifier/retriever against the gold labels
# ---------------------------------------------------------------------------
def score_orphan_predictions(preds: dict) -> dict:
    """preds: {case_id: 'orphaned'|'clean'}. Precision/recall for the 'orphaned' class vs gold.

    A prediction that is neither 'orphaned' nor 'clean' (None, a crash, 'malformed',
    'UNKNOWN', ...) is counted as `invalid` and is NEVER credited as a true negative — a
    verifier that crashes on a clean case must not silently inflate specificity. (Bug this
    guards: the old `g=='clean' and p != 'orphaned'` branch scored ANY non-orphaned
    prediction — including a crash/None — as a correct rejection, fabricating precision.)"""
    tp = fp = fn = tn = invalid = 0
    for c in ORPHAN_CASES:
        g, p = c["gold"], preds.get(c["id"])
        if p not in ("orphaned", "clean"):
            invalid += 1
        if g == "orphaned":
            if p == "orphaned":
                tp += 1
            else:                          # clean OR invalid prediction on an orphan = missed
                fn += 1
        else:  # g == "clean"
            if p == "orphaned":
                fp += 1
            elif p == "clean":
                tn += 1
            # an invalid prediction on a clean case is counted in `invalid` only — not a TN
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "invalid": invalid,
            "precision": prec, "recall": rec}


def score_referent_predictions(preds: dict) -> dict:
    """preds: {case_id: referent_id|'ABSTAIN'|'NONE'}. Exact-match accuracy vs gold,
    plus an abstain-correctness count (abstaining when gold==ABSTAIN is RIGHT)."""
    correct = sum(1 for c in REFERENT_CASES if preds.get(c["id"]) == c["gold"])
    return {"accuracy": correct / len(REFERENT_CASES), "correct": correct,
            "total": len(REFERENT_CASES)}


# ---------------------------------------------------------------------------
# Self-test: validate the ORACLE on the by-construction labels + positive controls
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    fails = []
    print("=== ORACLE validation (verdict must equal the by-construction gold) ===")
    for c in ORPHAN_CASES:
        r = run_orphan_oracle(c)
        ok = (r["verdict"] == c["gold"])
        pc = " [positive-control]" if c.get("positive_control") else ""
        print("  {0:32s} gold={1:9s} oracle={2:9s} {3}{4}".format(
            c["id"], c["gold"], r["verdict"], "OK" if ok else "*** MISMATCH ***", pc))
        if not ok:
            fails.append("orphan {0}: oracle said {1}, gold {2} (before_passed={3})".format(
                c["id"], r["verdict"], c["gold"], r["before_passed"]))

    # The oracle, fed the TRUE labels, must reproduce them perfectly (it IS the truth here).
    oracle_self = {c["id"]: run_orphan_oracle(c)["verdict"] for c in ORPHAN_CASES}
    s = score_orphan_predictions(oracle_self)
    print("  oracle vs gold: precision={0} recall={1} (must be 1.0/1.0); invalid={2}".format(
        s["precision"], s["recall"], s["invalid"]))
    if s["precision"] != 1.0 or s["recall"] != 1.0:
        fails.append("oracle did not perfectly reproduce the by-construction labels")
    if s["invalid"]:
        fails.append("oracle produced {0} invalid verdict(s) (neither orphaned nor clean)".format(s["invalid"]))

    # --- run9-class premise: execution catches kept-but-broken; a diff/removed-symbol check MISSES it ---
    r9 = next((c for c in ORPHAN_CASES if c["id"] == "kwarg-kept-but-broken"), None)
    if r9:
        ex = run_orphan_oracle(r9)["verdict"]
        diff_miss = not removed_public_symbol(r9["before"]["lib.py"], r9["change"]["lib.py"], "connect_timeout")
        print("\n=== run9-class premise (execution vs diff) ===")
        print("  execution oracle verdict      = {0}  (expect 'orphaned')".format(ex))
        print("  removed-symbol diff check MISS = {0}  (expect True: kwarg KEPT -> diff calls it clean)".format(diff_miss))
        if ex != "orphaned":
            fails.append("run9-class: execution oracle failed to flag the kept-but-broken tax")
        if not diff_miss:
            fails.append("run9-class: diff/removed-symbol check unexpectedly flagged it (premise broken)")

    print("\n=== REFERENT ground-truth set (labels; scored once a retriever exists) ===")
    for c in REFERENT_CASES:
        pc = " [positive-control]" if c.get("positive_control") else ""
        print("  {0:24s} gold={1:16s} prompt={2!r}{3}".format(c["id"], c["gold"], c["prompt"], pc))
    # sanity: an always-ABSTAIN retriever should get exactly the ambiguous case right, nothing else
    abstain_only = {c["id"]: "ABSTAIN" for c in REFERENT_CASES}
    print("  (sanity) always-ABSTAIN accuracy = {0:.2f} (expect only the ambiguous case correct)".format(
        score_referent_predictions(abstain_only)["accuracy"]))

    print("\n=== positive controls present ===")
    print("  orphan controls : obvious-orphan={0}  obvious-clean={1}".format(
        any(c.get("positive_control") and c["gold"] == "orphaned" for c in ORPHAN_CASES),
        any(c.get("positive_control") and c["gold"] == "clean" for c in ORPHAN_CASES)))
    print("  referent controls: named-referent={0}  must-abstain={1}".format(
        any(c.get("positive_control") and c["gold"] not in ("ABSTAIN", "NONE") for c in REFERENT_CASES),
        any(c.get("positive_control") and c["gold"] == "ABSTAIN" for c in REFERENT_CASES)))

    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nPASS: non-circular oracle reproduces all {0} by-construction orphan labels "
          "(code/data-semantic/invariant) at precision/recall 1.0; {1} referent ground-truth "
          "cases + positive controls present. Ground truth ready — retriever/verifier accuracy "
          "can now be measured against it.".format(len(ORPHAN_CASES), len(REFERENT_CASES)))
