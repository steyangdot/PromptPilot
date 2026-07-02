"""Stage A verify-side tests (docs/SESSION_MEMORY_V2_DESIGN.md §6-1/§6-2): contract-targeted
verification as a SECOND, collect-prevalidated pytest invocation with tri-state semantics, and
contract-quoting retry prompts. Real pytest subprocesses against a throwaway tree — no SLM, no
network, each invocation is a handful of trivial tests (fast)."""
import os
import tempfile
import textwrap

from prpt.verify import (VerifyResult, build_retry_prompt, collect_valid_targets,
                         run_verify_targets)


def _tree(**files):
    d = tempfile.mkdtemp(prefix="stage_a_verify_")
    for rel, body in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
    return d


PASSING = "def test_pass():\n    assert 1 + 1 == 2\n"
FAILING = "def test_fail():\n    assert 1 + 1 == 3\n"
BROKEN = "def test_syntax(:\n"          # SyntaxError -> collection error


def test_collect_valid_targets_validates_and_counts_unresolved():
    d = _tree(**{"tests/test_ok.py": PASSING})
    valid, unresolved = collect_valid_targets(d, [
        "tests/test_ok.py",                       # real file -> valid
        "tests/test_ok.py::test_pass",            # real node-id -> valid
        "tests/test_missing.py",                  # SLM-hallucinated path -> unresolved
        "tests/test_ok.py::test_nope",            # hallucinated test in a real file: file-level ok
        "-x",                                     # flag-like -> never executed, unresolved
    ])
    assert "tests/test_ok.py" in valid
    assert "tests/test_ok.py::test_pass" in valid
    assert "tests/test_missing.py" in unresolved
    assert "-x" in unresolved


def test_collect_total_failure_marks_all_unresolved():
    # nothing collectable at all (no test files) -> everything unresolved, nothing invented
    d = _tree(**{"readme.md": "hi\n"})
    valid, unresolved = collect_valid_targets(d, ["tests/test_ghost.py"])
    assert valid == []
    assert unresolved == ["tests/test_ghost.py"]


def test_run_verify_targets_tristate():
    d = _tree(**{"tests/test_ok.py": PASSING, "tests/test_bad.py": FAILING,
                 "tests/test_broken.py": BROKEN})
    green = run_verify_targets(d, ["tests/test_ok.py"])
    assert green.ran and green.passed                      # rc=0 -> green
    red = run_verify_targets(d, ["tests/test_bad.py"])
    assert red.ran and not red.passed and red.returncode == 1   # rc=1 -> a REAL contract red
    inv = run_verify_targets(d, ["tests/test_broken.py"])
    assert not inv.ran                                     # collection error -> tri-state
    assert "targeting-invalid" in (inv.skipped_reason or "")
    empty = run_verify_targets(d, [])
    assert not empty.ran and "no contract targets" in (empty.skipped_reason or "")


def test_second_invocation_never_masks_base_scope():
    # the contract invocation is a SEPARATE subprocess on explicit targets only: running the
    # broken file cannot change the green result of the ok file's own invocation.
    d = _tree(**{"tests/test_ok.py": PASSING, "tests/test_broken.py": BROKEN})
    inv = run_verify_targets(d, ["tests/test_broken.py"])
    base = run_verify_targets(d, ["tests/test_ok.py"])
    assert not inv.ran and base.ran and base.passed


def test_retry_prompt_quotes_contract():
    res = VerifyResult(ran=True, passed=False, command=["pytest", "-q"], returncode=1,
                       output_tail="E  AssertionError", targets=["tests/test_retries.py"])
    prompt = build_retry_prompt(res, ["httpx/_transports/default.py"], contracts=[
        {"feature": "retry-after", "contract": "Retry-After parsed as delta-seconds AND HTTP-date",
         "tests": ["tests/test_retries.py"]}])
    assert "Endangered contract" in prompt
    assert "retry-after: Retry-After parsed" in prompt
    assert "locking tests: tests/test_retries.py" in prompt
    # backward compatible: no contracts -> no block, no crash
    p2 = build_retry_prompt(res, ["a.py"])
    assert "Endangered contract" not in p2
