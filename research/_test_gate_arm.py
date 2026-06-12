#!/usr/bin/env python
r"""Self-test for the verify-gate measurement arm (chain_test_v2.run_verify_gate_turn).

Validates the gate's retry ACCOUNTING without spending on a real agent / pytest / git, by
monkeypatching the module-level seams (_run_one, _parse_one, _verify_run, ...). The thing
under test is: on a red verify, retry the agent (capped) and CHARGE the retry's tokens to
the turn so the arm's uncached/cost include the gate's cost — and skip/pass never retry.

Underscore-prefixed (not pytest-collected); run directly or via the tests/ CI bridge.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))            # research/
sys.path.insert(0, str(Path(__file__).parent.parent))     # repo root (for prpt)
import chain_test_v2 as C  # noqa: E402
from prpt.verify import VerifyResult  # noqa: E402

_fail = []


def check(name, got, want):
    if got != want:
        _fail.append("{0}: got {1!r}, want {2!r}".format(name, got, want))


_RETRY_USAGE = {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 0,
                "uncached_tokens": 100, "tool_calls": 3, "total_cost_usd": 0.01}


def run():
    seams = ("_run_one", "_parse_one", "_verify_run", "_verify_retry_prompt",
             "_quota_exhausted", "_git_changed_for_gate")
    orig = {k: getattr(C, k) for k in seams}
    try:
        C._run_one = lambda prompt, path, cwd, tool, session_id=None: (0.1, 0)
        C._parse_one = lambda path, tool: dict(_RETRY_USAGE)
        C._verify_retry_prompt = lambda result, changed: "fix the failing tests"
        C._quota_exhausted = lambda path, tool: False
        C._git_changed_for_gate = lambda cwd: ["httpx/_transports/default.py"]

        # Case 1: red -> retry -> green. One retry; retry tokens merged into the turn usage.
        seq = [VerifyResult(ran=True, passed=False, returncode=1),
               VerifyResult(ran=True, passed=True, returncode=0)]
        C._verify_run = lambda repo, changed: seq.pop(0)
        usage = {"input_tokens": 500, "output_tokens": 50, "cached_tokens": 0,
                 "uncached_tokens": 500, "tool_calls": 10, "total_cost_usd": 0.05}
        meta = C.run_verify_gate_turn("claude-code", Path("."), 1, 1, ".json", usage, max_retries=1)
        check("c1.retries", meta["retries"], 1)
        check("c1.verify_passed", meta["verify_passed"], True)
        check("c1.usage.uncached(+retry)", usage["uncached_tokens"], 600)
        check("c1.usage.tool_calls(+retry)", usage["tool_calls"], 13)
        check("c1.usage.cost(+retry)", round(usage["total_cost_usd"], 4), 0.06)
        check("c1.retry_tokens", meta["retry_tokens"]["uncached_tokens"], 100)

        # Case 2: green on the first verify -> no retry, usage untouched.
        C._verify_run = lambda repo, changed: VerifyResult(ran=True, passed=True, returncode=0)
        usage2 = {"input_tokens": 500, "output_tokens": 50, "cached_tokens": 0,
                  "uncached_tokens": 500, "tool_calls": 10}
        meta2 = C.run_verify_gate_turn("claude-code", Path("."), 1, 1, ".json", usage2)
        check("c2.retries", meta2["retries"], 0)
        check("c2.usage_unchanged", usage2["uncached_tokens"], 500)

        # Case 3: red persists past the cap -> stops at cap, verify still failed,
        # both retries charged.
        C._verify_run = lambda repo, changed: VerifyResult(ran=True, passed=False, returncode=1)
        usage3 = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                  "uncached_tokens": 0, "tool_calls": 0}
        meta3 = C.run_verify_gate_turn("claude-code", Path("."), 1, 1, ".json", usage3, max_retries=2)
        check("c3.retries_capped", meta3["retries"], 2)
        check("c3.verify_passed", meta3["verify_passed"], False)
        check("c3.usage.uncached(2 retries)", usage3["uncached_tokens"], 200)

        # Case 4: skip (verify couldn't run) -> never retries.
        C._verify_run = lambda repo, changed: VerifyResult(ran=False, skipped_reason="no framework")
        usage4 = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                  "uncached_tokens": 0, "tool_calls": 0}
        meta4 = C.run_verify_gate_turn("claude-code", Path("."), 1, 1, ".json", usage4)
        check("c4.retries", meta4["retries"], 0)
        check("c4.verify_ran", meta4["verify_ran"], False)
        check("c4.usage_unchanged", usage4["uncached_tokens"], 0)
    finally:
        for k, v in orig.items():
            setattr(C, k, v)

    if _fail:
        print("FAIL ({0}):".format(len(_fail)))
        for m in _fail:
            print("  -", m)
        sys.exit(1)
    print("PASS: verify-gate arm charges retry tokens to the turn, caps retries, and never "
          "retries on a pass/skip.")


if __name__ == "__main__":
    run()
