"""Tests for the verify-gate (prpt/verify.py).

The gate's verdict drives retries, so it is validated in BOTH directions (green tree ->
passed; red tree -> failed) AND on the load-bearing safety property: a SKIP (couldn't
verify) must never look like a FAILURE, or the gate would burn retries on un-verifiable
changes. Plus target-discovery / command-allow-list / path-safety unit checks.
"""
import importlib.util
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from prpt.core.types import RepoMetadata
from prpt.verify import (
    run_verify, run_gate, build_verify_command, discover_verify_targets,
    build_retry_prompt, should_verify, resolve_exit_code, VerifyResult,
    _safe_target, _is_test_file,
)

HAS_PYTEST = importlib.util.find_spec("pytest") is not None


def _repo(tmp, changed, fw="pytest"):
    return RepoMetadata(cwd=str(tmp), test_framework=fw, changed_files=list(changed))


def _write(tmp: Path, rel: str, body: str) -> str:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return rel


# --------------------------------------------------------------------------- pure logic

def test_is_test_file():
    assert _is_test_file("tests/test_foo.py")
    assert _is_test_file("src/foo_test.py")
    assert _is_test_file("ui/Button.test.ts")
    assert not _is_test_file("src/foo.py")


def test_discover_targets_changed_test_is_itself(tmp_path):
    _write(tmp_path, "tests/test_foo.py", "def test_x():\n    assert True\n")
    t = discover_verify_targets(_repo(tmp_path, ["tests/test_foo.py"]), ["tests/test_foo.py"])
    assert t == ["tests/test_foo.py"]


def test_discover_targets_source_maps_to_pair(tmp_path):
    _write(tmp_path, "src/foo.py", "x = 1\n")
    _write(tmp_path, "tests/test_foo.py", "def test_x():\n    assert True\n")
    t = discover_verify_targets(_repo(tmp_path, ["src/foo.py"]), ["src/foo.py"])
    assert t == ["tests/test_foo.py"]


def test_discover_targets_source_without_pair_is_empty(tmp_path):
    _write(tmp_path, "src/foo.py", "x = 1\n")
    assert discover_verify_targets(_repo(tmp_path, ["src/foo.py"]), ["src/foo.py"]) == []


def test_safe_target_rejects_flaglike_missing_and_escape(tmp_path):
    _write(tmp_path, "tests/test_foo.py", "def test_x():\n    assert True\n")
    assert _safe_target("tests/test_foo.py", str(tmp_path))
    assert not _safe_target("-x", str(tmp_path))            # flag injection
    assert not _safe_target("nope.py", str(tmp_path))       # missing
    assert not _safe_target("../escape.py", str(tmp_path))  # outside cwd


def test_safe_target_rejects_sibling_with_shared_prefix(tmp_path):
    # `repo` and `repo_evil` share a string prefix: a file in repo_evil must NOT count as
    # under repo. The old str.startswith containment check would have accepted it.
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _write(repo, "tests/test_a.py", "def test_a():\n    assert True\n")
    evil = tmp_path / "repo_evil"
    evil.mkdir()
    (evil / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    assert _safe_target("tests/test_a.py", str(repo))
    assert not _safe_target("../repo_evil/test_x.py", str(repo))


def test_build_command_allow_list():
    assert build_verify_command("junit", ["x"], False) is None       # unsupported
    assert build_verify_command(None, ["x"], False) is None          # no framework
    assert build_verify_command("pytest", [], False) is None         # no targets, not full
    cmd = build_verify_command("pytest", ["tests/test_foo.py"], False)
    assert cmd[:3] == [sys.executable, "-m", "pytest"]
    assert "-x" in cmd                                                # fail-fast
    assert "tests/test_foo.py" in cmd
    full = build_verify_command("pytest", [], True)                  # full suite, no targets ok
    assert full and "tests/test_foo.py" not in full
    # v1 is pytest-only: JS runners are detected but NOT run (no npx network/install risk).
    assert build_verify_command("jest", ["a.test.js"], False) is None
    assert build_verify_command("vitest", ["a.test.ts"], False) is None
    assert build_verify_command("mocha", ["a.spec.js"], False) is None


def test_should_verify_gates_on_intent_not_scope():
    # act turns of EVERY scope are verified; only non-editing routes skip.
    assert should_verify("act", True)            # broad/localized/pinpoint all route to "act"
    assert not should_verify("answer", True)
    assert not should_verify("clarify", True)
    assert not should_verify("passthrough", True)
    assert not should_verify("act", False)       # not requested
    assert should_verify("unknown", True)        # conservative: verify when route is unclear
    assert should_verify(None, True)


# --------------------------------------------------------------- real runs (both directions)

@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_run_verify_passes_on_green_tree(tmp_path):
    _write(tmp_path, "tests/test_ok.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    r = run_verify(_repo(tmp_path, ["tests/test_ok.py"]), ["tests/test_ok.py"])
    assert r.ran and r.passed and r.returncode == 0


@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_run_verify_fails_on_red_tree_and_builds_retry(tmp_path):
    _write(tmp_path, "tests/test_bad.py", "def test_bad():\n    assert False, 'boom'\n")
    r = run_verify(_repo(tmp_path, ["tests/test_bad.py"]), ["tests/test_bad.py"])
    assert r.ran and not r.passed and r.returncode not in (0, 5)
    rp = build_retry_prompt(r, ["tests/test_bad.py"])
    assert "did not pass verification" in rp
    assert "test_bad.py" in rp
    assert "SMALLEST change" in rp  # scope-preserving instruction (6c)


# ------------------------------------------------------------- skip is NOT a failure

def test_skip_when_no_framework_is_not_failure(tmp_path):
    r = run_verify(_repo(tmp_path, ["src/foo.py"], fw=None), ["src/foo.py"])
    assert not r.ran and not r.passed and r.skipped_reason


def test_skip_when_unsupported_framework(tmp_path):
    r = run_verify(_repo(tmp_path, ["src/foo.py"], fw="junit"), ["src/foo.py"])
    assert not r.ran and "not supported" in (r.skipped_reason or "")


@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_skip_when_no_changed_targets(tmp_path):
    _write(tmp_path, "src/foo.py", "x = 1\n")  # changed source, no test pair
    r = run_verify(_repo(tmp_path, ["src/foo.py"]), ["src/foo.py"])
    assert not r.ran and "no changed test targets" in (r.skipped_reason or "")


@pytest.mark.skipif(not (HAS_PYTEST and shutil.which("git")), reason="git + pytest required")
def test_untracked_new_test_is_discovered_and_run(tmp_path):
    # "add a unit test" creates an UNTRACKED file -> git diff (the adapter's source) sees
    # nothing, so changed_files is empty. The gate must still find + run it via ls-files.
    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True)
    g("init", "-q"); g("config", "user.email", "t@t.t"); g("config", "user.name", "t")
    _write(tmp_path, "tests/test_timeout_new.py", "def test_t():\n    assert True\n")  # never added
    r = run_verify(_repo(tmp_path, []), [])  # adapter reported NO changed files
    assert r.ran and r.passed and "tests/test_timeout_new.py" in r.targets


@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_pytest_rc5_no_tests_collected_is_skip(tmp_path):
    # a "test" file with no test functions -> pytest exit 5 -> skip, NOT a failure
    _write(tmp_path, "tests/test_empty.py", "x = 1  # no test functions here\n")
    r = run_verify(_repo(tmp_path, ["tests/test_empty.py"]), ["tests/test_empty.py"])
    assert not r.ran and "no tests collected" in (r.skipped_reason or "")


# ----------------------------------------------------------------- run_gate orchestration

class _FakeAdapter:
    """Stand-in for the downstream tool. Each .run() applies `on_run` (the 'fix') and
    records the call; last_modified_files is what the gate verifies against."""
    def __init__(self, modified, on_run=None):
        self.last_modified_files = list(modified)
        self._on_run = on_run
        self.calls = 0

    def run(self, prompt, args):
        self.calls += 1
        if self._on_run:
            self._on_run()
        return 0


def _val_test(tmp_path):
    """A test whose pass/fail is controlled by a sibling val.txt (no import-path issues)."""
    d = tmp_path / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / "val.txt").write_text("bad", encoding="utf-8")
    (d / "test_timeout_v.py").write_text(
        "import pathlib\n"
        "def test_v():\n"
        "    assert (pathlib.Path(__file__).parent / 'val.txt').read_text() == 'ok'\n",
        encoding="utf-8")
    return d / "val.txt"


@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_gate_retries_until_green(tmp_path):
    val = _val_test(tmp_path)  # starts red
    fa = _FakeAdapter(["tests/test_timeout_v.py"],
                      on_run=lambda: val.write_text("ok", encoding="utf-8"))  # retry fixes it
    result, retry_exit = run_gate(fa, None, _repo(tmp_path, ["tests/test_timeout_v.py"]), retries=2)
    assert fa.calls == 1            # one retry sufficed (stopped early)
    assert result.ran and result.passed and retry_exit == 0


@pytest.mark.skipif(not HAS_PYTEST, reason="pytest runner unavailable")
def test_gate_retry_cap_exhausts_on_persistent_red(tmp_path):
    _val_test(tmp_path)  # stays red; adapter never fixes it
    fa = _FakeAdapter(["tests/test_timeout_v.py"], on_run=None)
    result, retry_exit = run_gate(fa, None, _repo(tmp_path, ["tests/test_timeout_v.py"]), retries=2)
    assert fa.calls == 2           # exactly the cap, then stop
    assert result.ran and not result.passed


def test_gate_skip_never_retries(tmp_path):
    # no framework -> skip -> adapter.run must never be called, retry_exit stays None
    fa = _FakeAdapter(["src/foo.py"])
    result, retry_exit = run_gate(fa, None, _repo(tmp_path, ["src/foo.py"], fw=None), retries=3)
    assert fa.calls == 0 and not result.ran and retry_exit is None


def test_resolve_exit_code_failed_verify_overrides_zero_agent_exit():
    # THE BUG (codex P1, PR #36): agent + last retry both exit 0 as processes, but tests
    # still fail -> exit must be NONZERO so CI/scripts/harness can gate on the verify result.
    failed = VerifyResult(ran=True, passed=False, returncode=1)
    assert resolve_exit_code(0, 0, failed) == 1
    assert resolve_exit_code(0, None, failed) == 1     # failed on first try, no retry ran
    # timeout (rc 124) is still surfaced as nonzero
    assert resolve_exit_code(0, 0, VerifyResult(ran=True, passed=False, returncode=124)) == 124


def test_resolve_exit_code_pass_and_skip_preserve_exit():
    passed = VerifyResult(ran=True, passed=True, returncode=0)
    assert resolve_exit_code(0, None, passed) == 0
    assert resolve_exit_code(0, 0, passed) == 0
    skipped = VerifyResult(ran=False, skipped_reason="no framework")
    assert resolve_exit_code(0, None, skipped) == 0       # skip is not a failure
    assert resolve_exit_code(3, None, skipped) == 3       # preserves a real agent failure
    assert resolve_exit_code(0, None, None) == 0          # gate didn't run
    # a crashed retry is preserved when verify itself passed
    assert resolve_exit_code(0, 2, passed) == 2


def test_log_schema_accepts_verify_field(tmp_path):
    # build_log_event has a fixed schema (not kwargs-passthrough); the new verify field must
    # be an explicit, JSON-safe param (L1 critique). VerifyResult.to_log() is that payload.
    from prpt.core.utils import build_log_event
    from prpt.verify import VerifyResult
    vr = VerifyResult(ran=True, passed=False, returncode=1, command=["pytest"], targets=["t.py"])
    ev = build_log_event(mode="wrapped", tool="claude-code", normalizer="slm", cwd=str(tmp_path),
                         repo=_repo(tmp_path, []), raw_prompt="x", final_prompt="y", exit_code=1,
                         verify=vr.to_log())
    assert ev["verify"]["ran"] is True and ev["verify"]["passed"] is False
    assert ev["verify"]["returncode"] == 1
