#!/usr/bin/env python
r"""Self-test for the captured-end-state hook (chain_test_v2.capture_end_state)
and its gold-path scorer (score_endstate.score_captured_endstate).

Two layers:
  A. score_captured_endstate (pure): synthetic captured artifacts -> verdicts/scores,
     covering both transports, the re-add direction, and the no-test case. The captured
     diff is COMPLETE, so a transport with no hunk is BUG (still seeded), never UNKNOWN.
  B. capture_end_state (integration): a real temp git repo + a live `-k timeout` pytest
     run -> assert the diff / new_files / pytest fields are captured. Gracefully skips the
     git/pytest assertions when those tools are unavailable.

Underscore-prefixed so pytest does not auto-collect it; run directly or via the tests/
CI bridge (tests/test_research_selftests.py). Exits nonzero on failure.
"""
from __future__ import annotations
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))            # research/
sys.path.insert(0, str(Path(__file__).parent.parent))     # repo root (for prpt import)
import score_endstate as S  # noqa: E402

_failures = []

BUG = '            extensions={k: v for k, v in request.extensions.items() if k != "timeout"},'
FIX = '            extensions=request.extensions,'
DEFAULT = 'httpx/_transports/default.py'


def _diff(*hunks):
    head = f"diff --git a/{DEFAULT} b/{DEFAULT}\n--- a/{DEFAULT}\n+++ b/{DEFAULT}\n"
    return head + "".join(hunks)


def _sync_fix():
    return "@@ -244,7 +244,7 @@ class HTTPTransport\n-" + BUG + "\n+" + FIX + "\n"


def _async_fix():
    return "@@ -388,7 +388,7 @@ class AsyncHTTPTransport\n-" + BUG + "\n+" + FIX + "\n"


def _sync_bug():
    return "@@ -244,7 +244,7 @@ class HTTPTransport\n+" + BUG + "\n"


def _check(name, got, want):
    if got != want:
        _failures.append(f"{name}: got {got!r}, want {want!r}")


def test_score_captured():
    # A. both fixed + a regression test file + green timeout pytest -> 1.0
    es = {"diff": _diff(_sync_fix(), _async_fix()), "new_files": ["tests/test_timeouts.py"],
          "pytest_rc": 0, "pytest_passed": True}
    r = S.score_captured_endstate(es); s, _ = S.endstate_score(r)
    _check("A.sync", r["sync"], "FIXED"); _check("A.async", r["async"], "FIXED")
    _check("A.score", s, 1.0); _check("A.source", r["source"], "captured")

    # B. sync-only fix, no timeout test (rc 5) -> async BUG (unchanged seeded base) -> 0.5
    es = {"diff": _diff(_sync_fix()), "new_files": [], "pytest_rc": 5,
          "pytest_passed": False, "pytest_no_match": True}
    r = S.score_captured_endstate(es); s, _ = S.endstate_score(r)
    _check("B.sync", r["sync"], "FIXED"); _check("B.async", r["async"], "BUG"); _check("B.score", s, 0.5)

    # C. empty diff, no pytest -> both BUG (still seeded) -> 0.0
    r = S.score_captured_endstate({"diff": "", "new_files": []}); s, _ = S.endstate_score(r)
    _check("C.sync", r["sync"], "BUG"); _check("C.async", r["async"], "BUG"); _check("C.score", s, 0.0)

    # D. both fixed + test file but pytest FAILED (rc 1) -> 0.75 (no test credit on a red run)
    es = {"diff": _diff(_sync_fix(), _async_fix()), "new_files": ["tests/test_timeouts.py"],
          "pytest_rc": 1, "pytest_passed": False}
    r = S.score_captured_endstate(es); s, _ = S.endstate_score(r)
    _check("D.score", s, 0.75)

    # E. residual bug re-added on sync (+BUG) -> sync BUG; async fixed -> 0.5
    es = {"diff": _diff(_sync_bug(), _async_fix()), "new_files": [], "pytest_rc": 5, "pytest_passed": False}
    r = S.score_captured_endstate(es); s, _ = S.endstate_score(r)
    _check("E.sync", r["sync"], "BUG"); _check("E.async", r["async"], "FIXED"); _check("E.score", s, 0.5)


def test_capture_integration():
    if not shutil.which("git"):
        print("  [skip] git unavailable -> capture integration not run")
        return
    import chain_test_v2 as C  # heavy import (pulls prpt); only needed for this layer
    tmp = Path(tempfile.mkdtemp(prefix="endstate_cap_"))
    try:
        def run(*a):
            subprocess.run(list(a), cwd=tmp, capture_output=True)
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@t.t")
        run("git", "config", "user.name", "t")
        (tmp / "mod.py").write_text("x = 1\n", encoding="utf-8")
        run("git", "add", "."); run("git", "commit", "-qm", "base")
        (tmp / "mod.py").write_text("x = 2  # edited\n", encoding="utf-8")              # tracked edit
        (tmp / "test_timeout_x.py").write_text(
            "def test_timeout_pass():\n    assert True\n", encoding="utf-8")            # untracked test
        has_pytest = importlib.util.find_spec("pytest") is not None
        es = C.capture_end_state(str(tmp), tmp / "out", "x", 1, run_pytest=has_pytest)
        if not (tmp / "out" / "endstate_x_run1.json").exists():
            _failures.append("capture: artifact file not written")
        if "edited" not in es.get("diff", ""):
            _failures.append("capture: diff missing the tracked edit")
        if "test_timeout_x.py" not in es.get("new_files", []):
            _failures.append(f"capture: new_files missing untracked test: {es.get('new_files')}")
        if has_pytest:
            if es.get("pytest_rc") != 0:
                _failures.append(f"capture: -k timeout pytest not green (rc={es.get('pytest_rc')}, "
                                 f"tail={es.get('pytest_tail')!r})")
            if not es.get("pytest_passed"):
                _failures.append("capture: pytest_passed not True on a green timeout test")
        else:
            print("  [skip] pytest unavailable -> pytest assertions not run")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_score_captured()
    test_capture_integration()
    if _failures:
        print(f"FAIL ({len(_failures)} assertion(s)):")
        for m in _failures:
            print("  -", m)
        sys.exit(1)
    print("PASS: captured-end-state scorer (5 cases incl. re-add + no-test) + "
          "capture_end_state integration (diff + new_files + live -k timeout pytest).")
