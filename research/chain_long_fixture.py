"""
chain_long — a long, dependent coding chain for the COMPACTION-REGIME TEST.

Design: docs/COMPACTION_REGIME_TEST.md

Purpose
-------
Push the per-call context occupancy past Codex's ~233k compaction threshold (90% of
the 258,400 gpt-5.5 Codex window) so that **native auto-compaction actually engages**
— the one regime we have never measured.

REWORK (2026-06-18, after pilot 1 hung)
---------------------------------------
Pilot 1 (the original "write tests + run them" fixture) WEDGED: the agent executed
httpx's hang-prone network/timeout tests (no real server) -> pytest hung -> 184s
command-timeouts -> retries -> ~60 model calls/turn -> per-turn cost exploded to
~9.8M tokens and orphaned pytest procs piled up until the run hung at T12. Yet
per-call occupancy only climbed ~11.6k/turn (62k -> 178k over 11 turns), so it would
not have compacted until ~turn 16. Lessons applied here:
  1. NO test execution. Every turn ends with an explicit "do not run any tests"
     guard, and there are NO "write+run tests" turns -> removes the hang + the churn.
  2. Drive occupancy via LARGE FILE READS (httpx's big modules) + an accumulating,
     referential transcript -> occupancy should climb faster and STABLY (bounded
     ~handful of calls/turn, ~1-2M tokens/turn instead of ~10M).
The re-pilot measures the new occupancy curve; adjust turn count if it doesn't cross
~233k with margin.

Schema matches the other chains: {raw, expected_files, expected_action, referential}.
Each `raw` ends with the no-tests guard; for `builtin` (resume) the turn-1 guard
persists, and for `with_session` (fresh exec/turn) each turn carries its own.
"""
from __future__ import annotations

# Stage-0 fix: the old wording ("Edit the code/files only — do NOT run ... any tests")
# was misread by an agent as "run NO commands at all" and it bailed on the final
# refactor (with_memory run4, T13) rather than inspecting the file. Disambiguate:
# inspection/search is allowed; only test EXECUTION is forbidden (the load-bearing
# intent — httpx's network tests hang with no server, the pilot-1 wedge).
_GUARD = (" You MAY read, grep, and inspect any files to do this well; "
          "do NOT EXECUTE tests (no pytest or test-suite runs).")

def _t(raw, files, action="modify", ref=True):
    return {"raw": raw + _GUARD, "expected_files": files,
            "expected_action": action, "referential": ref}

CHAIN_LONG = {
    "id": "chain_long",
    "label": "Long dependent chain (compaction-regime test, read-heavy, no test exec)",
    "description": (
        "Incrementally builds + documents a resilience/observability layer on httpx, "
        "reading large modules each turn to grow per-call context toward Codex's ~233k "
        "compaction threshold. NO test execution (pilot 1 hung on httpx's network tests). "
        "See docs/COMPACTION_REGIME_TEST.md."
    ),
    "turns": [
        # --- timeout overrides (each turn reads a large module first) ---
        _t("Read httpx/_client.py in full, then add a per-request connect_timeout "
           "override to the sync Client that falls back to the client's default timeout "
           "when not provided.",
           ["httpx/_client.py", "httpx/_config.py"], ref=False),
        _t("Mirror that same connect_timeout override onto AsyncClient in httpx/_client.py "
           "using the identical fallback pattern.",
           ["httpx/_client.py"]),
        _t("Read httpx/_config.py in full, then add a read_timeout override alongside "
           "connect_timeout on both clients with the same fallback-to-default semantics.",
           ["httpx/_config.py", "httpx/_client.py"]),
        # --- retry-after (reads the transport + utils) ---
        _t("Read httpx/_transports/default.py in full, then add Retry-After response "
           "header support to the retry path, reusing the timeout fallback approach where "
           "it makes sense.",
           ["httpx/_transports/default.py", "httpx/_client.py"]),
        _t("Extend that Retry-After handling to accept an HTTP-date as well as "
           "delta-seconds; read httpx/_utils.py for the existing date/parse helpers.",
           ["httpx/_transports/default.py", "httpx/_utils.py"]),
        _t("Cap the computed retry delay at a configurable maximum (default 60s), in "
           "httpx/_config.py and the transport.",
           ["httpx/_config.py", "httpx/_transports/default.py"]),
        # --- timing + stats (reads _models.py, big) ---
        _t("Read httpx/_models.py in full, then record the per-request elapsed wall time "
           "on the Response object.",
           ["httpx/_models.py", "httpx/_client.py"], ref=False),
        _t("Expose that elapsed timing through an optional event hook on the Client; read "
           "httpx/_client.py for the existing hook plumbing.",
           ["httpx/_client.py"]),
        _t("Add a new httpx/_stats.py with a small collector that aggregates the elapsed "
           "timings (count, mean, max) across requests, and wire it into the Client; read "
           "httpx/_client.py and httpx/_models.py for the integration points.",
           ["httpx/_stats.py", "httpx/_client.py", "httpx/__init__.py"]),
        # --- pool limits (parallels the timeout overrides) ---
        _t("Add a pool_size override on the Client following the same override-with-"
           "fallback style we used for the timeouts; read httpx/_config.py and "
           "httpx/_client.py.",
           ["httpx/_config.py", "httpx/_client.py"]),
        _t("Wire the pool_size override through to the underlying transport; read "
           "httpx/_transports/default.py.",
           ["httpx/_transports/default.py"]),
        # --- the big referential refactor (reads + references the whole session) ---
        _t("Read httpx/_client.py and httpx/_config.py again, then refactor the "
           "connect_timeout, read_timeout, retry, and pool_size overrides we added this "
           "session into a single ResilienceConfig dataclass in _config.py.",
           ["httpx/_config.py", "httpx/_client.py"]),
        _t("Migrate both the sync and async clients in httpx/_client.py to accept and use "
           "that ResilienceConfig instead of the individual kwargs.",
           ["httpx/_client.py", "httpx/_config.py"]),
        # NOTE: trimmed 18 -> 13 turns for the N=3 run. Calibration (pilot 2) showed
        # native compaction fires ~turn 10-11; turns 11-13 give the in-regime window.
        # The dropped turns 14-18 were trailing doc/comment passes (cheap to lose; they
        # were the most expensive tail turns under builtin re-feed, ~16-21M tokens each).
    ],
}
