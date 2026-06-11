#!/usr/bin/env python
r"""Regression test for score_endstate.py per-site attribution.

Locks the fix for the codex-bot PR #33 finding: a global (unattributed) git-diff
fix signal let a ONE-transport fix credit BOTH sites and mask a partial fix.
Underscore-prefixed so pytest does not auto-collect it (it builds synthetic
transcripts on disk). Run directly: python research/_test_score_endstate.py
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import score_endstate as S  # noqa: E402

BUG = '            extensions={k: v for k, v in request.extensions.items() if k != "timeout"},'
FIX = '            extensions=request.extensions,'


def _cmd(output: str) -> str:
    return json.dumps({"type": "item.completed",
                       "item": {"type": "command_execution", "aggregated_output": output}})


def _file_change(path: str, kind: str = "update") -> str:
    return json.dumps({"type": "item.completed",
                       "item": {"type": "file_change",
                                "changes": [{"path": path, "kind": kind}]}})


def _sync_hunk():
    return ("diff --git a/httpx/_transports/default.py b/httpx/_transports/default.py\n"
            "@@ -244,7 +244,7 @@ class HTTPTransport\n" + "-" + BUG + "\n+" + FIX + "\n")


def _async_hunk():
    return ("@@ -388,7 +388,7 @@ class AsyncHTTPTransport\n" + "-" + BUG + "\n+" + FIX + "\n")


def _write_run(d: Path, arm: str, run: int, events: list[str]):
    (d / f"{arm}_run{run}.json").write_text("[]", encoding="utf-8")        # summary stub (for main glob)
    (d / f"run{run}_{arm}_t1.jsonl").write_text("\n".join(events), encoding="utf-8")


def _run_case(name: str, events: list[str], exp_sync: str, exp_async: str, exp_score):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "codex" / "chain1"
        d.mkdir(parents=True)
        _write_run(d, "x", 1, events)
        r = S.extract_run(d, "x", 1)
        score, _conf = S.endstate_score(r)
        ok = (r["sync"] == exp_sync and r["async"] == exp_async and score == exp_score)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: sync={r['sync']} async={r['async']} "
              f"score={score} (expected sync={exp_sync} async={exp_async} score={exp_score})")
        return ok


def main():
    print("score_endstate per-site attribution regression (PR #33 codex-bot finding):")
    results = []

    # 1. PARTIAL fix: only the SYNC hunk is in the diff; an rg still shows the ASYNC bug.
    #    The bug: a global diff-fix signal would mark async FIXED too. Must now be async=BUG.
    results.append(_run_case(
        "partial sync-only fix -> async must stay BUG",
        [_cmd(_sync_hunk()),
         _cmd("httpx/_transports/default.py:391:" + BUG)],
        exp_sync="FIXED", exp_async="BUG", exp_score=0.5))

    # 2. BOTH transports fixed (sync+async hunks) + a passing test -> 1.0 (must not regress).
    results.append(_run_case(
        "both transports fixed + passing test -> 1.0",
        [_cmd(_sync_hunk() + _async_hunk()),
         _file_change("C:/projects/httpx/tests/test_timeouts.py", "update"),
         _cmd("tests/test_timeouts.py::test_timeout_forwarded PASSED\n1 passed in 0.4s")],
        exp_sync="FIXED", exp_async="FIXED", exp_score=1.0))

    # 3. Stale early rg-BUG then a later fix hunk -> latest wins, FIXED (no regression of v1 fix).
    results.append(_run_case(
        "stale rg-BUG then fix hunk -> FIXED",
        [_cmd("httpx/_transports/default.py:247:" + BUG + "\nhttpx/_transports/default.py:391:" + BUG),
         _cmd(_sync_hunk() + _async_hunk()),
         _file_change("C:/projects/httpx/tests/test_timeouts.py"),
         _cmd("2 passed in 0.5s")],
        exp_sync="FIXED", exp_async="FIXED", exp_score=1.0))

    print("\nRESULT:", "ALL PASS" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
