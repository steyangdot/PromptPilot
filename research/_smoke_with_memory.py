"""Stubbed end-to-end smoke for the with_memory arm (no network, no quota).

Monkeypatches the tool-exec + SLM-network boundaries of chain_test_v2 and drives a real
run_chain_once(..., "with_memory", ...) over a 3-turn synthetic chain to prove the arm
actually executes turn-to-turn: the ledger carries a turn-1 contract forward so a turn-2
refactor sees the guard (had_history True), and a timed-out turn-3 SKIPS the paid ledger
call (PR#44 #10) while keeping ledger_ok True. Also exercises print_memory_ab (#13).

Run:  python research/_smoke_with_memory.py
"""
import os
import sys
import tempfile
import types

os.environ["CAPTURE_END_STATE"] = "0"   # skip the live git-diff/pytest end-state capture

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import memory_ledger as ml          # noqa: E402
import chain_test_v2 as ct          # noqa: E402

_fail = []
def ok(name, cond):
    if not cond:
        _fail.append(name)

# --- stub the network/exec boundaries --------------------------------------
_extract_calls = {"n": 0}

def _fake_extract(raw, memory_record, changed_files, target_files, judge=None):
    """Canned contract extractor (replaces the gpt-5.4-nano call)."""
    _extract_calls["n"] += 1
    return ([{"feature": "timeout-overrides",
              "contract": "sync/async clients accept connect_timeout/read_timeout kwargs",
              "files": ["httpx/_client.py"], "tests": ["tests/client/test_client.py"],
              "symbols": ["connect_timeout", "read_timeout"]}], 0.0002, True)

ml._slm_extract_contracts = _fake_extract           # update_ledger calls this module-locally

def _fake_prepare_no_session(raw, cwd, tool):
    spec = types.SimpleNamespace(
        target_files=["httpx/_client.py"], memory_record="did X", scope="localized", risk="low")
    return {
        "optimized": "[rewritten] " + raw, "grounded": raw, "raw": raw,
        "intent": "fix", "scope": "localized", "had_history": False,
        "_normalizer": types.SimpleNamespace(_last_spec=spec),
    }

def _fake_run_one(prompt, out_path, cwd, tool, session_id=None):
    out_path.write_text("{}", encoding="utf-8")
    rc = 124 if "DESIGNED_TIMEOUT" in prompt else 0     # turn 3 simulates a timeout
    return 0.5, rc

def _fake_parse_one(out_path, tool):
    return {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 0,
            "uncached_tokens": 1000, "tool_calls": 3}

def _fake_score_turn(turn_def, before, before_globs, cwd, usage):
    return {"success": 1.0, "bailed": False, "changed": ["httpx/_client.py"]}

ct.prepare_no_session = _fake_prepare_no_session
ct._run_one = _fake_run_one
ct._parse_one = _fake_parse_one
ct.score_turn = _fake_score_turn
ct._quota_exhausted = lambda out_path, tool: False
ct.ledger_judge_available = lambda: True
ct.reset_repo = lambda cwd: None
ct.clear_session = lambda cwd: None
ct.reap_claude_orphans = lambda: 0
ct.snapshot_files = lambda cwd, files: {}
ct.snapshot_globs = lambda cwd, globs: {}
# _git_modified_files is imported inside record_to_memory from prpt.adapters.shell — stub there.
try:
    import prpt.adapters.shell as _sh
    _sh._git_modified_files = lambda cwd: ["httpx/_client.py"]
except Exception:
    pass

CHAIN = {
    "id": "smoke_mem",
    "label": "stub", "description": "stub",
    "turns": [
        {"raw": "Add connect_timeout/read_timeout kwargs to httpx/_client.py.",
         "expected_files": ["httpx/_client.py"], "expected_action": "add", "expected_globs": []},
        {"raw": "Refactor httpx/_client.py to take a single ResilienceConfig object.",
         "expected_files": ["httpx/_client.py"], "expected_action": "refactor", "expected_globs": []},
        {"raw": "DESIGNED_TIMEOUT: long task that will be killed in httpx/_client.py.",
         "expected_files": ["httpx/_client.py"], "expected_action": "edit", "expected_globs": []},
    ],
}

with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as out:
    from pathlib import Path
    ct.HTTPX_DIR = repo
    ml.clear_ledger(repo)
    results = ct.run_chain_once(CHAIN, "codex", "with_memory", 1, Path(out))

    ok("arm ran all 3 turns", len(results) == 3)
    ok("every turn carries ledger_ok", all("ledger_ok" in t for t in results))
    # turn 1: empty ledger -> no memory prefix injected
    ok("T1 had_history False (ledger started empty)", results[0]["had_history"] is False)
    # turn 2: refactor touching httpx/_client.py -> turn-1 contract surfaced via the guard
    ok("T2 had_history True (turn-1 contract carried forward)", results[1]["had_history"] is True)
    ok("T2 ledger_ok True", results[1]["ledger_ok"] is True)
    # turn 3: timed out -> record_to_memory SKIPPED (PR#44 #10)
    ok("T3 timed_out True", results[2]["timed_out"] is True)
    ok("T3 ledger_slm_cost 0 (record skipped on timeout)", results[2]["ledger_slm_cost"] == 0.0)
    ok("extractor called exactly twice (T1,T2 — not on timed-out T3)", _extract_calls["n"] == 2)
    ok("ledger persisted with the contract", "timeout-overrides" in ml.load_ledger(repo)["contracts"])

# print_memory_ab smoke (PR#44 #13): synthetic aggs, just confirm it renders without error
_agg = lambda succ, tot, unc: [{"success_mean": succ, "input_tokens_mean": tot,
                                "uncached_input_mean": unc}]
print("--- print_memory_ab smoke ---")
ct.print_memory_ab(_agg(1.0, 50000, 5000), _agg(1.0, 12000, 1200), "codex")

if _fail:
    print("\nFAIL:")
    for f in _fail:
        print("  -", f)
    sys.exit(1)
print("\nPASS: with_memory arm runs end-to-end (stubbed) — contract carry-forward, "
      "timeout-skip, ledger_ok, A/B render.")
