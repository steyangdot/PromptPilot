"""Self-test for the ProjectState-Ledger + Refactor-Guard MVP (research/memory_ledger.py).

Pure stdlib (no pytest, no real SLM — the model call is injected via FakeJudge). Exits
non-zero on failure. The load-bearing test (`test_run3_distance_independent`) replays the
N=5 run3 failure shape: a contract established at TURN 1, buried under 4 later contracts,
is STILL surfaced at a turn-13 refactor — which the MAX_TURNS=4 recency window could not do.

Run:  python research/_test_memory_ledger.py
"""
import json
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import memory_ledger as ml  # noqa: E402

_failures = []


def check(name, got, want):
    if got != want:
        _failures.append("{0}: got {1!r}, want {2!r}".format(name, got, want))


def truthy(name, cond):
    if not cond:
        _failures.append("{0}: expected truthy, got {1!r}".format(name, cond))


def _spec(target_files=None, memory_record="", scope="localized", risk="low"):
    return types.SimpleNamespace(
        target_files=target_files or [], memory_record=memory_record, scope=scope, risk=risk)


class FakeJudge:
    """Stand-in for OpenAiJudge: returns a fixed contracts payload, no network."""
    def __init__(self, contracts):
        self.contracts = contracts

    def __call__(self, prompt, timeout=90):
        return json.dumps({"contracts": self.contracts}), 0.0, 0.0


def _seed(cwd, contracts_by_turn):
    led = ml.load_ledger(cwd)
    for turn, cs in contracts_by_turn:
        ml.merge_contracts(led, cs, turn=turn)
    ml.save_ledger(cwd, led)


# --- merge / persistence ----------------------------------------------------
def test_merge_upsert():
    led = {"version": 1, "contracts": {}}
    ml.merge_contracts(led, [{"feature": "Timeout Overrides", "contract": "c1",
                              "files": ["a.py"], "symbols": ["connect_timeout"]}], turn=1)
    truthy("feature id normalized to kebab", "timeout-overrides" in led["contracts"])
    ml.merge_contracts(led, [{"feature": "timeout-overrides", "tests": ["t.py"],
                              "files": ["a.py", "b.py"]}], turn=2)
    c = led["contracts"]["timeout-overrides"]
    check("union-merge files (dedup, ordered)", c["files"], ["a.py", "b.py"])
    check("tests added", c["tests"], ["t.py"])
    check("symbols preserved", c["symbols"], ["connect_timeout"])
    check("recency turn updated", c["turn"], 2)


def test_recency_bound():
    led = {"version": 1, "contracts": {}}
    for t in range(1, ml.MAX_CONTRACTS + 11):
        ml.merge_contracts(led, [{"feature": "f{0}".format(t), "contract": "c"}], turn=t)
    check("bounded to MAX_CONTRACTS", len(led["contracts"]), ml.MAX_CONTRACTS)
    truthy("oldest evicted", "f1" not in led["contracts"])
    truthy("newest kept", "f{0}".format(ml.MAX_CONTRACTS + 10) in led["contracts"])


def test_update_ledger_with_fake_slm():
    with tempfile.TemporaryDirectory() as d:
        j = FakeJudge([{"feature": "timeout-overrides",
                        "contract": "sync+async client request APIs accept connect_timeout/read_timeout",
                        "files": ["httpx/_client.py"], "tests": ["tests/client/test_client.py"],
                        "symbols": ["connect_timeout", "read_timeout"]}])
        led = ml.update_ledger(d, "add a connect_timeout override to the sync client",
                               _spec(target_files=["httpx/_client.py"]),
                               ["httpx/_client.py"], turn=1, judge=j)
        truthy("ledger persisted to sidecar", ml._ledger_path(d).exists())
        truthy("contract recorded via SLM path", "timeout-overrides" in led["contracts"])
        check("reload matches", ml.load_ledger(d)["contracts"].keys() == led["contracts"].keys(), True)


# --- the load-bearing test: distance-independent recall (run3) ---------------
def test_run3_distance_independent():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [
            # TURN 1: the contract that run3 orphaned
            (1, [{"feature": "timeout-overrides",
                  "contract": "sync+async client request APIs accept connect_timeout/read_timeout kwargs",
                  "files": ["httpx/_client.py", "httpx/_config.py"],
                  "tests": ["tests/client/test_client.py", "tests/client/test_async_client.py"],
                  "symbols": ["connect_timeout", "read_timeout"]}]),
            # 4 LATER contracts that bury turn-1 well past any MAX_TURNS=4 recency window
            (4, [{"feature": "retry-after", "contract": "Retry-After parsed + capped",
                  "files": ["httpx/_transports/default.py"], "symbols": ["Retry-After"]}]),
            (7, [{"feature": "elapsed-timing", "contract": "Response.elapsed + _stats.py",
                  "files": ["httpx/_models.py", "httpx/_stats.py"], "symbols": ["elapsed"]}]),
            (10, [{"feature": "pool-size", "contract": "pool_size wired to transport",
                   "files": ["httpx/_transports/default.py"], "symbols": ["pool_size"]}]),
            (12, [{"feature": "resilience-config", "contract": "ResilienceConfig unifies overrides",
                   "files": ["httpx/_config.py"], "symbols": ["ResilienceConfig"]}]),
        ])
        # TURN 13 — the exact run3 refactor that orphaned the timeout tests
        raw = ("Migrate both the sync and async clients in httpx/_client.py to accept and use "
               "that ResilienceConfig instead of the individual kwargs.")
        spec = _spec(target_files=["httpx/_client.py", "httpx/_config.py"], scope="broad")
        cl = ml.refactor_guard_checklist(d, raw, spec)
        truthy("guard fired at the refactor", bool(cl))
        truthy("turn-1 timeout-overrides surfaced (recency window would have dropped it)",
               "timeout-overrides" in cl)
        truthy("its orphaned tests surfaced", "test_client.py" in cl)
        truthy("preserve-or-migrate instruction present", "migrate" in cl.lower())


def test_run4_retry_after_surfaced():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [
            (1, [{"feature": "timeout-overrides", "contract": "connect/read timeout kwargs",
                  "files": ["httpx/_client.py"], "symbols": ["connect_timeout"]}]),
            (4, [{"feature": "retry-after",
                  "contract": "Retry-After parsed as delta-seconds AND HTTP-date, then capped at max_retry_delay",
                  "files": ["httpx/_transports/default.py", "httpx/_utils.py"],
                  "tests": ["tests/test_retries.py"],
                  "symbols": ["Retry-After", "parse_http_date", "max_retry_delay"]}]),
        ])
        raw = "Refactor the retry path in httpx/_transports/default.py into the ResilienceConfig."
        spec = _spec(target_files=["httpx/_transports/default.py"], scope="broad")
        cl = ml.refactor_guard_checklist(d, raw, spec)
        truthy("retry-after contract surfaced", "retry-after" in cl)
        truthy("header-parsing obligation present", ("HTTP-date" in cl) or ("parse_http_date" in cl))


def test_no_false_fire_on_unrelated_turn():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "timeout-overrides", "contract": "connect/read timeout kwargs",
                        "files": ["httpx/_client.py"], "symbols": ["connect_timeout", "read_timeout"]}])])
        # plain, non-refactor add, unrelated file/symbols -> guard must NOT surface the contract
        raw = "Add a brief module docstring to httpx/_status_codes.py."
        spec = _spec(target_files=["httpx/_status_codes.py"], scope="localized")
        cl = ml.refactor_guard_checklist(d, raw, spec)
        check("no spurious guard fire", cl, "")


def test_overlap_fires_without_keyword():
    # the review's example: a refactor phrased without a trigger word, caught by file overlap
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "timeout-overrides", "contract": "connect/read timeout kwargs",
                        "files": ["httpx/_client.py"], "symbols": ["connect_timeout", "read_timeout"]}])])
        raw = "Change the clients to take a single config object."   # no refactor keyword
        spec = _spec(target_files=["httpx/_client.py"], scope="localized")  # but file overlaps the contract
        cl = ml.refactor_guard_checklist(d, raw, spec)
        truthy("file-overlap fired the guard without a keyword", "timeout-overrides" in cl)


def test_is_refactor_signals():
    truthy("migrate keyword", ml._is_refactor("migrate the clients to X", _spec()))
    truthy("refactor keyword", ml._is_refactor("refactor X into Y", _spec()))
    truthy("broad scope (no keyword)", ml._is_refactor("change clients to one config", _spec(scope="broad")))
    check("plain localized add is not refactor", ml._is_refactor("add a docstring", _spec(scope="localized")), False)


def test_clear():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "x", "contract": "c", "files": ["a.py"]}])])
        truthy("sidecar exists before clear", ml._ledger_path(d).exists())
        ml.clear_ledger(d)
        check("sidecar removed", ml._ledger_path(d).exists(), False)
        check("empty ledger after clear", ml.load_ledger(d)["contracts"], {})


if __name__ == "__main__":
    for t in (test_merge_upsert, test_recency_bound, test_update_ledger_with_fake_slm,
              test_run3_distance_independent, test_run4_retry_after_surfaced,
              test_no_false_fire_on_unrelated_turn, test_overlap_fires_without_keyword,
              test_is_refactor_signals, test_clear):
        t()
    if _failures:
        print("FAIL ({0} assertion(s)):".format(len(_failures)))
        for m in _failures:
            print("  -", m)
        sys.exit(1)
    print("PASS: ledger merge/bound, SLM-path (mocked), distance-independent run3/run4 recall, "
          "no-false-fire, keyword-free overlap, clear.")
