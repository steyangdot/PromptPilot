"""
Self-test: the harness usage parsers must expose UNCACHED tokens as a
first-class field, separately from gross input and cache_read.

Why this exists: the 2026-06-07 re-test proved that GROSS per-turn input is ~95%
cached re-reads, so reporting gross overstates real cost ~10x. The robust metric
is UNCACHED = (new tokens billed at ~full input price) = gross_input - cache_read.
PR #32 added uncached instrumentation at the chain_test_v2 layer (recomputing
input-cached inline); this pins the SAME definition in the parser itself so the
canonical field can't silently drift back to gross and is reused, not re-derived.

Run:  python research/_test_usage_parser.py
(pure stdlib, no pytest required; exits non-zero on failure)
"""
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                       # research/ (for agentic_variety_test)
sys.path.insert(0, os.path.dirname(_HERE))      # repo root — agentic_variety_test imports
                                                # the top-level `prpt` package, so a clean
                                                # checkout (prpt not pip-installed) needs it
from agentic_variety_test import (  # noqa: E402
    parse_usage_claude, parse_usage, claude_cost, codex_cost,
)

_failures = []


def check(name, got, want):
    if got != want:
        _failures.append(f"{name}: got {got!r}, want {want!r}")


def test_claude_uncached_split():
    # Claude Code --output-format json: input_tokens = NEW uncached tokens,
    # cache_creation = new tokens written to cache, cache_read = served from cache.
    usage = {
        "input_tokens": 30,                    # new, not cached
        "cache_creation_input_tokens": 70,     # new, written to cache  -> still uncached cost
        "cache_read_input_tokens": 900,        # served from cache       -> the ~95% re-read
        "output_tokens": 5,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"usage": usage, "num_turns": 3, "total_cost_usd": 0.123}, f)
        path = f.name
    try:
        from pathlib import Path
        u = parse_usage_claude(Path(path))
    finally:
        os.unlink(path)

    # uncached = input + cache_creation = 100 (NOT 1000 gross, NOT 30 input-only)
    check("claude.uncached_tokens", u["uncached_tokens"], 100)
    check("claude.cached_tokens", u["cached_tokens"], 900)
    check("claude.input_tokens(gross)", u["input_tokens"], 1000)
    # the load-bearing invariant: gross == uncached + cached
    check("claude.invariant", u["input_tokens"], u["uncached_tokens"] + u["cached_tokens"])


def test_codex_uncached_split():
    # Codex turn.completed usage: input_tokens = TOTAL prompt tokens (incl. cached),
    # cached_input_tokens = the cached subset. UNCACHED = total - cached.
    events = [
        {"type": "turn.completed",
         "usage": {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 5}},
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "item.completed", "item": {"type": "agent_message"}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        path = f.name
    try:
        from pathlib import Path
        u = parse_usage(Path(path))
    finally:
        os.unlink(path)

    check("codex.uncached_tokens", u["uncached_tokens"], 100)
    check("codex.cached_tokens", u["cached_tokens"], 900)
    check("codex.input_tokens(gross)", u["input_tokens"], 1000)
    check("codex.invariant", u["input_tokens"], u["uncached_tokens"] + u["cached_tokens"])
    check("codex.tool_calls", u["tool_calls"], 1)


def test_cost_uses_uncached_not_gross():
    # A record that is 90% cache_read must cost far less than its gross would imply.
    u = {"input_tokens": 1000, "cached_tokens": 900, "uncached_tokens": 100,
         "output_tokens": 0}
    # codex: 100 uncached * $1.10/M + 900 cached * $0.275/M
    want = 100 * 1.10 / 1e6 + 900 * 0.275 / 1e6
    got = codex_cost(u)
    if abs(got - want) > 1e-12:
        _failures.append(f"codex_cost uses uncached: got {got}, want {want}")
    # if it had wrongly used GROSS as uncached, cost would be ~10x higher
    gross_wrong = 1000 * 1.10 / 1e6
    if abs(got - gross_wrong) < 1e-9:
        _failures.append("codex_cost appears to bill gross input, not uncached")

    # claude_cost prefers total_cost_usd when present
    check("claude_cost prefers reported $", claude_cost({**u, "total_cost_usd": 0.42}), 0.42)


def test_cost_fallback_without_uncached_field():
    # Records that predate the uncached_tokens field must still cost correctly via the
    # gross-minus-cached fallback (no KeyError, same value).
    u = {"input_tokens": 1000, "cached_tokens": 900, "output_tokens": 0}
    want = 100 * 1.10 / 1e6 + 900 * 0.275 / 1e6
    if abs(codex_cost(u) - want) > 1e-12:
        _failures.append("codex_cost fallback (no uncached_tokens) broke")


def test_empty_record_has_uncached_field():
    from pathlib import Path
    u = parse_usage_claude(Path(tempfile.gettempdir()) / "does_not_exist_xyz.json")
    if "uncached_tokens" not in u:
        _failures.append("empty/failed claude record missing uncached_tokens field")


if __name__ == "__main__":
    for t in (test_claude_uncached_split, test_codex_uncached_split,
              test_cost_uses_uncached_not_gross, test_cost_fallback_without_uncached_field,
              test_empty_record_has_uncached_field):
        t()
    if _failures:
        print("FAIL ({} assertion(s)):".format(len(_failures)))
        for msg in _failures:
            print("  -", msg)
        sys.exit(1)
    print("PASS: usage parsers expose uncached tokens correctly (claude + codex), "
          "cost bills uncached, fallback holds, invariant gross == uncached + cached.")
