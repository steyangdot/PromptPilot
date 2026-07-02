"""Stage A acceptance — Tier 0 deterministic micro-fixture
(docs/SESSION_MEMORY_V2_STAGE_A_ACCEPTANCE.md §4.1).

Drives the WIRED orchestrator `prpt.verify.run_contract_gate` against a throwaway source/test tree
plus a hand-seeded ledger — no agent, no SLM, no network, 0 frontier tokens. Each invocation is a
handful of trivial real `pytest` subprocesses (fast). This EXTENDS the primitive-level unit tests
(tests/test_stage_a_verify.py, tests/test_memory.py): those test collect/run/guard in isolation;
this tests orchestrator + ledger + tree as the one unit the harness and product both call.

Arms (gate ids in the doc): A1 destructive-kwarg RED at causing turn (green before) [G0.1];
A2 additive-but-buggy RED at causing turn [G0.2]; A3 hallucinated path / A4 hallucinated node-id /
A5 corrupted test -> unresolved, never red/green [G0.3]; A6 unlocked counted, no invocation [G0.4];
A7 correct-keep -> green [G0.5]; A8a/A8b mixed set — bad target never poisons the valid one, which
still reports its true verdict [G0.3]; A9 contract-quoting retry prompt [G0.6]; A-order reads the
PRE-turn ledger only [G0.7].
"""
import os
import tempfile
import textwrap

from prpt.verify import run_contract_gate, build_retry_prompt
from prpt.memory import save_ledger


# --- tree + ledger helpers --------------------------------------------------
def _write(d, rel, body):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p) or d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))


def _tree(**files):
    d = tempfile.mkdtemp(prefix="stage_a_accept_")
    for rel, body in files.items():
        _write(d, rel, body)
    return d


def _seed(cwd, contracts):
    """Persist a hand-built ledger for `cwd` (same sidecar run_contract_gate->load_ledger reads)."""
    assert save_ledger(cwd, {"version": 1, "contracts": contracts})


# --- source / test fixtures (self-contained; test imports the sibling module) ---
SRC_OK = "def make_client(timeout=5.0):\n    return {'timeout': timeout}\n"
SRC_NOKWARG = "def make_client():\n    return {}\n"                     # destructive: kwarg dropped
SRC_BUGGY = "def make_client(timeout=5.0):\n    return {'timeout': 0}\n"  # kept surface, wrong behavior
TEST_TIMEOUT = ("from client import make_client\n"
                "def test_timeout_kwarg():\n"
                "    assert make_client(timeout=9)['timeout'] == 9\n")
TEST_FAIL = "def test_always_fails():\n    assert False\n"
BROKEN = "def test_syntax(:\n"                                          # SyntaxError -> collect error

_TIMEOUT_CONTRACT = {
    "connect-timeout": {"contract": "make_client(timeout=...) is kept and honored",
                        "files": ["client.py"], "symbols": ["timeout"],
                        "tests": ["test_client.py::test_timeout_kwarg"]}}
_RAW = "refactor client.py to simplify make_client timeout handling"


# --- G0.1 destructive-kwarg -------------------------------------------------
def test_a1_destructive_kwarg_red_at_causing_turn():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, _TIMEOUT_CONTRACT)
    before = run_contract_gate(d, _RAW, None)
    assert before.result.ran and before.result.passed                      # GREEN immediately before
    assert "test_client.py::test_timeout_kwarg" in before.valid_targets
    _write(d, "client.py", SRC_NOKWARG)                                    # the causing turn
    after = run_contract_gate(d, _RAW, None)
    assert after.result.ran and not after.result.passed                    # RED at the causing turn
    assert after.result.returncode == 1


# --- G0.2 additive-but-buggy (the run9 class) -------------------------------
def test_a2_additive_but_buggy_red_at_causing_turn():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, _TIMEOUT_CONTRACT)
    assert run_contract_gate(d, _RAW, None).result.passed                  # green before
    _write(d, "client.py", SRC_BUGGY)                                      # surface kept, behavior broken
    r = run_contract_gate(d, _RAW, None)
    assert r.result.ran and not r.result.passed                            # RED at the causing turn


# --- G0.3 bad targets degrade to targeting-invalid, never red/green ---------
def test_a3_hallucinated_path_unresolved():
    d = _tree(**{"mod.py": "x = 1\n"})
    _seed(d, {"feat-x": {"contract": "keep mod.py", "files": ["mod.py"],
                         "tests": ["tests/test_ghost.py::test_missing"]}})
    r = run_contract_gate(d, "refactor mod.py", None)
    assert "tests/test_ghost.py::test_missing" in r.unresolved
    assert r.valid_targets == []
    assert not r.result.ran                                                # never red, never green


def test_a4_hallucinated_nodeid_unresolved():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, {"connect-timeout": {"contract": "x", "files": ["client.py"], "symbols": ["timeout"],
                                  "tests": ["test_client.py::test_MISSING"]}})
    r = run_contract_gate(d, _RAW, None)
    assert "test_client.py::test_MISSING" in r.unresolved                  # real file, fake node-id
    assert r.valid_targets == []
    assert not r.result.ran


def test_a5_corrupted_test_unresolved_never_red():
    d = _tree(**{"client.py": SRC_OK, "test_broken.py": BROKEN})
    _seed(d, {"connect-timeout": {"contract": "x", "files": ["client.py"], "symbols": ["client.py"],
                                  "tests": ["test_broken.py"]}})
    r = run_contract_gate(d, "refactor client.py", None)
    assert "test_broken.py" in r.unresolved                                # collect error -> nothing valid
    assert r.valid_targets == []
    assert not r.result.ran                                                # never red on a broken test


# --- G0.4 unlocked contract: counted, not run -------------------------------
def test_a6_unlocked_counted_no_invocation():
    d = _tree(**{"client.py": SRC_OK})
    _seed(d, {"connect-timeout": {"contract": "x", "files": ["client.py"], "tests": []}})
    r = run_contract_gate(d, "refactor client.py", None)
    assert "connect-timeout" in r.unlocked
    assert r.valid_targets == [] and not r.result.ran
    assert r.result.skipped_reason == "no contract targets"


# --- G0.5 correct-keep -> green ---------------------------------------------
def test_a7_correct_keep_green():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, _TIMEOUT_CONTRACT)
    r = run_contract_gate(d, "add a docstring to client.py make_client", None)
    assert "connect-timeout" in r.hits
    assert r.result.ran and r.result.passed


# --- G0.3 mixed set: a bad target must not poison a co-present valid one -----
def test_a8a_mixed_set_valid_passes():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, {
        "connect-timeout": {"contract": "x", "files": ["client.py"], "symbols": ["timeout"],
                            "tests": ["test_client.py::test_timeout_kwarg"]},
        "ghost": {"contract": "x", "files": ["client.py"], "symbols": ["client.py"],
                  "tests": ["test_client.py::test_MISSING"]}})
    r = run_contract_gate(d, "refactor client.py make_client timeout", None)
    assert "test_client.py::test_MISSING" in r.unresolved                  # bad -> unresolved
    assert "test_client.py::test_timeout_kwarg" in r.valid_targets         # valid -> runs
    assert r.result.ran and r.result.passed                                # GREEN; batch not aborted


def test_a8b_mixed_set_valid_fails_still_red():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT, "test_fail.py": TEST_FAIL})
    _seed(d, {
        "good": {"contract": "x", "files": ["client.py"], "symbols": ["client.py"],
                 "tests": ["test_fail.py::test_always_fails"]},
        "ghost": {"contract": "x", "files": ["client.py"], "symbols": ["client.py"],
                  "tests": ["test_client.py::test_MISSING"]}})
    r = run_contract_gate(d, "refactor client.py", None)
    assert "test_client.py::test_MISSING" in r.unresolved                  # bad -> unresolved
    assert "test_fail.py::test_always_fails" in r.valid_targets
    assert r.result.ran and not r.result.passed                            # RED: regression not masked


# --- G0.6 contract-quoting retry prompt -------------------------------------
def test_a9_contract_quoting_retry_prompt():
    d = _tree(**{"client.py": SRC_BUGGY, "test_client.py": TEST_TIMEOUT})
    _seed(d, _TIMEOUT_CONTRACT)
    cg = run_contract_gate(d, _RAW, None)
    assert cg.result.ran and not cg.result.passed                          # red run
    prompt = build_retry_prompt(cg.result, ["client.py"], contracts=cg.contracts)
    assert "Endangered contract" in prompt
    assert "connect-timeout" in prompt
    assert "locking tests:" in prompt


# --- G0.7 the gate reads the PRE-turn ledger only ---------------------------
def test_aorder_reads_preturn_ledger_only():
    d = _tree(**{"client.py": SRC_OK, "test_client.py": TEST_TIMEOUT})
    _seed(d, {})                                                           # empty ledger entering the turn
    r0 = run_contract_gate(d, "add a make_client timeout kwarg to client.py", None)
    assert r0.hits == [] and not r0.result.ran                            # this turn's own contract is NOT seen
    _seed(d, _TIMEOUT_CONTRACT)                                            # simulate update_ledger landing it
    r1 = run_contract_gate(d, _RAW, None)
    assert "connect-timeout" in r1.hits                                    # a LATER turn now sees it
