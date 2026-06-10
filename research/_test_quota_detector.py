"""Regression check for `_quota_exhausted` — codex false-positive fix (2026-06-09).

The codex branch must fire ONLY on a real turn.failed/error EVENT whose message
carries usage-limit phrasing — NOT on the agent's own timeout/retry-themed prose
(which legitimately contains "try again at", "rate limit", "retry-after" on a
timeout/retry chain). Underscore-prefixed so pytest does not auto-collect it
(it imports the research harness). Run: python research/_test_quota_detector.py
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as C  # noqa: E402


def _jsonl(events):
    p = Path(tempfile.mkstemp(suffix=".jsonl")[1])
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


def _json(obj):
    p = Path(tempfile.mkstemp(suffix=".json")[1])
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


fails = 0


def check(name, path, tool, expect):
    global fails
    got = C._quota_exhausted(path, tool)
    ok = got == expect
    print(("  PASS " if ok else "  FAIL ") + f"{name}: got={got} expect={expect}")
    if not ok:
        fails += 1


# 1) THE REGRESSION: a NON-quota turn.failed + agent prose containing 'try again at'
#    / 'rate limit'. OLD code returned True (false abort); fix must return False.
check("codex: non-quota turn.failed + retry-themed agent prose", _jsonl([
    {"type": "item.completed", "item": {"type": "agent_message",
        "text": "Adding retry-after: on a 429 rate limit we back off and try again at the next window."}},
    {"type": "item.completed", "item": {"type": "command_execution",
        "output": "tests/test_retries.py ... rate limit handling ok"}},
    {"type": "turn.failed", "error": {"message": "tool execution error: command timed out after 300s"}},
]), "codex", False)

# 2) REAL usage-limit cap in a turn.failed event -> must fire.
check("codex: real usage-limit turn.failed", _jsonl([
    {"type": "thread.started", "thread_id": "x"},
    {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Upgrade to Pro ... try again at 5:36 PM."}},
]), "codex", True)

# 3) REAL cap surfaced as type:error with top-level message -> must fire.
check("codex: usage-limit type:error", _jsonl([
    {"type": "error", "message": "usage limit reached; try again at 10:40 PM"},
]), "codex", True)

# 4) Healthy successful codex run -> must NOT fire.
check("codex: benign success", _jsonl([
    {"type": "thread.started", "thread_id": "x"},
    {"type": "item.completed", "item": {"type": "command_execution", "output": "All tests pass"}},
    {"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 0, "output_tokens": 3}},
]), "codex", False)

# claude-code generic branch (single JSON object).
check("claude: rate_limit_error", _json({"is_error": True, "api_error_status": 429, "result": "rate_limit_error"}), "claude-code", True)
check("claude: benign", _json({"subtype": "success", "result": "fixed the timeout; added a test"}), "claude-code", False)

print("\nRESULT:", "ALL PASS" if not fails else f"{fails} FAILURE(S)")
raise SystemExit(1 if fails else 0)
