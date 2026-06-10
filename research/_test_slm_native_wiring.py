"""Throwaway wiring test for the slm_native / stacked arms (no SLM/agent calls).

Monkeypatches every heavy dependency of run_chain_once so we can assert, for
free, that:
  - slm_native uses prepare_no_session (SLM rewrite, no bounded history)
  - stacked    uses prepare_with_session (rewrite + bounded history)
  - both set uses_builtin -> native session_id captured on T1, passed to T2
  - slm_native does NOT record_to_session (continuity is native, not in-prompt)
  - stacked DOES record_to_session
  - slm_native pays slm_cost (it is not raw/builtin)

Run:  python research/_test_slm_native_wiring.py
"""
from __future__ import annotations
import json
from pathlib import Path

import chain_test_v2 as C

calls = {"prep": [], "record": [], "run_one_session_ids": [], "slm_cost_calls": 0}


def _fake_prep_factory(name):
    def _prep(raw, cwd, tool):
        calls["prep"].append(name)
        return {
            "raw": raw, "rewrite": raw, "grounded": raw + " (grounded)",
            "optimized": raw + " (optimized)", "intent": "act",
            "scope": "localized", "had_history": (name == "with_session"),
            "referential": None, "gate_skipped": False,
            "_normalizer": object(), "_normalized": object(),
        }
    return _prep


def _fake_run_one(prompt, out_path, cwd, tool, session_id=None):
    # Record the session_id we were handed (None on T1, sid on T2+).
    calls["run_one_session_ids"].append(session_id)
    # Emulate claude-code JSON output carrying a session_id so the capture path
    # (run_chain_once lines ~855-877) can read it back for the next turn.
    Path(out_path).write_text(
        json.dumps({"session_id": "test-sid-123", "result": "ok"}),
        encoding="utf-8")
    return (1.23, 0)


def _patch():
    C.prepare_no_session = _fake_prep_factory("no_session")
    C.prepare_with_session = _fake_prep_factory("with_session")
    C.prepare_raw = _fake_prep_factory("raw")
    C._run_one = _fake_run_one
    C._quota_exhausted = lambda out_path, tool: False
    # Mirror the real parsed-usage shape: parse_usage_claude/parse_usage always
    # include cached_tokens (and claude adds total_cost_usd). The cost helpers
    # depend on these keys.
    C._parse_one = lambda out_path, tool: {
        "input_tokens": 100, "cached_tokens": 80, "output_tokens": 50,
        "tool_calls": 3, "total_cost_usd": 0.01}
    C.score_turn = lambda td, b, bg, cwd, usage: {
        "success": 1.0, "bailed": False, "changed": ["x.py"]}
    def _fake_record(cwd, raw, prepared):
        calls["record"].append(raw)
    C.record_to_session = _fake_record
    def _fake_cost(raw, grounded):
        calls["slm_cost_calls"] += 1
        return 0.0001
    C.slm_cost_estimate = _fake_cost
    C.snapshot_files = lambda cwd, files: set()
    C.snapshot_globs = lambda cwd, globs: set()
    C.reset_repo = lambda cwd: None
    C.clear_session = lambda cwd: None
    C.reap_claude_orphans = lambda: 0
    C._extract_primary_model = lambda out_path: "claude-opus-test"


def _chain():
    return {"id": "chainX", "label": "test", "turns": [
        {"raw": "turn one", "expected_files": ["a.py"],
         "expected_action": "modify", "expected_globs": []},
        {"raw": "turn two", "expected_files": ["b.py"],
         "expected_action": "modify", "expected_globs": []},
    ]}


def _reset():
    calls["prep"].clear(); calls["record"].clear()
    calls["run_one_session_ids"].clear(); calls["slm_cost_calls"] = 0


def main():
    _patch()
    out = Path("./_wiring_tmp"); out.mkdir(exist_ok=True)
    failures = []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    # --- slm_native (handoff "STACKED": SLM rewrite + native resume) ---
    _reset()
    print("[slm_native]")
    C.run_chain_once(_chain(), "claude-code", "slm_native", 1, out)
    check(calls["prep"] == ["no_session", "no_session"],
          "uses prepare_no_session per turn (SLM rewrite, no bounded history)")
    check(calls["record"] == [],
          "does NOT record_to_session (native continuity, not in-prompt)")
    check(calls["run_one_session_ids"] == [None, "test-sid-123"],
          "captures native session_id on T1, passes --resume id to T2")
    check(calls["slm_cost_calls"] == 2, "pays slm_cost per turn (not raw/builtin)")

    # --- stacked (double memory: rewrite + bounded session + native resume) ---
    _reset()
    print("[stacked]")
    C.run_chain_once(_chain(), "claude-code", "stacked", 1, out)
    check(calls["prep"] == ["with_session", "with_session"],
          "uses prepare_with_session per turn (rewrite + bounded history)")
    check(calls["record"] == ["turn one", "turn two"],
          "DOES record_to_session each turn (bounded session active)")
    check(calls["run_one_session_ids"] == [None, "test-sid-123"],
          "also captures + passes native session_id (double memory)")

    # --- builtin (raw + native resume) sanity: no slm_cost, no record ---
    _reset()
    print("[builtin]")
    C.run_chain_once(_chain(), "claude-code", "builtin", 1, out)
    check(calls["prep"] == ["raw", "raw"], "uses prepare_raw (no rewrite)")
    check(calls["record"] == [], "does NOT record_to_session")
    check(calls["slm_cost_calls"] == 0, "pays NO slm_cost (raw/builtin)")

    import shutil
    shutil.rmtree(out, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S)")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
