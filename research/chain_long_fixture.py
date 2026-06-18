"""
chain_long — a long, dependent coding chain for the COMPACTION-REGIME TEST.

Design: docs/COMPACTION_REGIME_TEST.md

Purpose
-------
Every prior PromptPilot benchmark is *sub-threshold*: per-call context never
approaches Codex's ~258,400-token window, so native `codex exec resume` re-feeds
the whole transcript freely and the bounded session wins ~4x. This chain is built
to push the **per-call context occupancy past ~233k** (90% of the window) so that
**native auto-compaction actually engages** — the one regime we have never measured.

How it crosses the threshold
----------------------------
- ~24 turns, each doing real, incremental httpx work on top of the previous turns.
- Heavily *referential*: most turns back-reference earlier work ("the same pattern",
  "the helper you added", "every test we wrote this session"), so the session memory
  is genuinely load-bearing AND the re-fed transcript grows monotonically in `builtin`.
- The final arc (T19-T24) refactors/migrates/documents *everything built so far*,
  forcing the agent to hold the whole session in context — the turns most likely to
  cross the compaction threshold.

The exact turn count needed to cross ~233k is unknown — that is what the calibration
PILOT measures (see the design doc §4.0). 24 turns is a starting length with margin;
trim/extend after the pilot plots the real per-call-occupancy curve.

Schema matches the other chains in chain_test_v2.py:
    {raw, expected_files, expected_action, referential, [expected_globs]}
`expected_files` feeds the (secondary) file-hash scorer; the PRIMARY quality signal
for this test is the LLM continuity judge (research/judge_continuity.py), because the
file-hash / end-state scorers are too coarse to see continuity loss over a long chain.
"""
from __future__ import annotations

CHAIN_LONG = {
    "id": "chain_long",
    "label": "Long dependent chain (compaction-regime test, ~24 turns)",
    "description": (
        "Incrementally builds a resilience+observability layer on httpx "
        "(per-request timeout overrides -> retry-after -> timing/stats -> pool "
        "limits), then refactors it all into a unified ResilienceConfig and "
        "documents it. Designed to push per-call context past Codex's ~233k "
        "compaction threshold so native auto-compaction engages. See "
        "docs/COMPACTION_REGIME_TEST.md."
    ),
    "turns": [
        # --- Arc 1: per-request timeout overrides (establish the pattern) ---
        {
            "raw": "add a per-request connect_timeout override to the sync Client that falls back to the client's default timeout when not provided",
            "expected_files": ["httpx/_client.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": False,
        },
        {
            "raw": "add a unit test for that connect_timeout override",
            "expected_files": ["tests/client/test_timeouts.py", "tests/test_timeouts.py", "tests/client/test_client.py"],
            "expected_globs": ["tests/**/test_timeout*.py", "tests/**/test_client*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "mirror the same connect_timeout override onto the AsyncClient using the identical fallback pattern",
            "expected_files": ["httpx/_client.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "add an async unit test mirroring the sync connect_timeout test you just wrote",
            "expected_files": ["tests/client/test_async_client.py", "tests/client/test_timeouts.py"],
            "expected_globs": ["tests/**/test_async*.py", "tests/**/test_timeout*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "add a read_timeout override alongside the connect_timeout one, reusing the same fallback-to-default pattern on both clients",
            "expected_files": ["httpx/_client.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "extend the timeout tests to cover the read_timeout override for both sync and async",
            "expected_files": ["tests/client/test_timeouts.py", "tests/client/test_async_client.py"],
            "expected_globs": ["tests/**/test_timeout*.py", "tests/**/test_async*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        # --- Arc 2: retry-after (builds on the timeout work) ---
        {
            "raw": "add Retry-After header support to the retry path, reusing the timeout fallback helper where it makes sense",
            "expected_files": ["httpx/_transports/default.py", "httpx/_client.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "handle Retry-After given as an HTTP-date, not just delta-seconds, in that retry support",
            "expected_files": ["httpx/_transports/default.py", "httpx/_utils.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "cap the computed retry delay at a configurable maximum, defaulting to 60 seconds",
            "expected_files": ["httpx/_transports/default.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "write tests covering all three Retry-After behaviors we just added (seconds, http-date, and the delay cap)",
            "expected_files": ["tests/test_retries.py", "tests/client/test_retries.py"],
            "expected_globs": ["tests/**/test_retr*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        # --- Arc 3: timing + stats observability ---
        {
            "raw": "add request timing instrumentation that records the elapsed wall time of each request on the Response object",
            "expected_files": ["httpx/_client.py", "httpx/_models.py"],
            "expected_action": "modify",
            "referential": False,
        },
        {
            "raw": "expose that elapsed timing through an optional event hook callers can register",
            "expected_files": ["httpx/_client.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "add a small stats collector that aggregates the elapsed timings (count, mean, max) across requests",
            "expected_files": ["httpx/_stats.py", "httpx/_client.py", "httpx/__init__.py"],
            "expected_globs": ["httpx/_stats.py", "httpx/_client.py", "httpx/__init__.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "write tests for the stats collector you just added",
            "expected_files": ["tests/test_stats.py", "tests/client/test_client.py"],
            "expected_globs": ["tests/**/test_stat*.py", "tests/**/test_client*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        # --- Arc 4: connection-pool limits (parallels the timeout overrides) ---
        {
            "raw": "add a pool_size override on the Client, following the same override-with-fallback style we used for the timeouts",
            "expected_files": ["httpx/_client.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "wire that pool_size override through to the underlying transport",
            "expected_files": ["httpx/_transports/default.py", "httpx/_client.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "emit a warning when the connection pool is exhausted",
            "expected_files": ["httpx/_transports/default.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "test the pool_size override and the pool-exhaustion warning",
            "expected_files": ["tests/test_limits.py", "tests/client/test_client.py"],
            "expected_globs": ["tests/**/test_limit*.py", "tests/**/test_pool*.py", "tests/**/test_client*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        # --- Arc 5: the big referential refactor (forces whole-session context) ---
        {
            "raw": "refactor the connect_timeout, read_timeout, retry, and pool_size overrides we added in this session into a single ResilienceConfig dataclass",
            "expected_files": ["httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "migrate both the sync and async clients to accept and use that ResilienceConfig instead of the individual kwargs",
            "expected_files": ["httpx/_client.py", "httpx/_config.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "update every test we wrote in this session to construct the new ResilienceConfig instead of passing the individual kwargs",
            "expected_files": ["tests/client/test_timeouts.py", "tests/test_retries.py", "tests/test_stats.py", "tests/test_limits.py"],
            "expected_globs": ["tests/**/test_timeout*.py", "tests/**/test_retr*.py", "tests/**/test_stat*.py", "tests/**/test_limit*.py", "tests/**/test_async*.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "add a CHANGELOG entry summarizing all the resilience and observability changes from this session",
            "expected_files": ["CHANGELOG.md"],
            "expected_globs": ["CHANGELOG*", "docs/CHANGELOG*"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "add concise inline comments to each change we made this session explaining the fallback-to-default rationale",
            "expected_files": ["httpx/_client.py", "httpx/_config.py", "httpx/_transports/default.py"],
            "expected_action": "modify",
            "referential": True,
        },
        {
            "raw": "write a short docs/resilience.md describing the ResilienceConfig and the timing/stats hooks we built in this session",
            "expected_files": ["docs/resilience.md"],
            "expected_globs": ["docs/resilience*", "docs/*.md"],
            "expected_action": "modify",
            "referential": True,
        },
    ],
}
