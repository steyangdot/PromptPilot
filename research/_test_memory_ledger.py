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
        led, cost, ok = ml.update_ledger(d, "add a connect_timeout override to the sync client",
                                         _spec(target_files=["httpx/_client.py"]),
                                         ["httpx/_client.py"], turn=1, judge=j)
        truthy("update_ledger reports ok on a successful extraction", ok)
        check("cost is a float", isinstance(cost, float), True)
        truthy("ledger persisted to sidecar", ml._ledger_path(d).exists())
        truthy("contract recorded via SLM path", "timeout-overrides" in led["contracts"])

    # ok=False path: a judge that returns empty text (simulates missing OPENAI_API_KEY)
    with tempfile.TemporaryDirectory() as d2:
        class EmptyJudge:
            def __call__(self, prompt, timeout=90):
                return "", 0.0, 0.0
        _led, _c, ok2 = ml.update_ledger(d2, "x", _spec(), [], turn=1, judge=EmptyJudge())
        check("empty SLM output -> ok=False (loud no-op, not silent)", ok2, False)
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


# --- hardening (PR#44 /code-review) -----------------------------------------
def test_merge_tolerates_scalar_and_empty_payloads():
    # PR#44 #1/#3: untrusted SLM output with scalar/None files + a bare string + empties
    # must NOT crash and must be sanitized (strip, drop empty), never iterated as chars.
    led = {"version": 1, "contracts": {}}
    ml.merge_contracts(led, [
        {"feature": "f-scalar", "contract": "c", "files": "a.py",          # str, not list
         "symbols": 123, "tests": None},                                   # int + None
        {"feature": "f-empty", "contract": "c", "files": ["  ", "", "b.py", "b.py"]},
        "not-a-dict", 42, None,                                             # non-dict items skipped
    ], turn=1)
    check("scalar file string coerced to single-element list", led["contracts"]["f-scalar"]["files"], ["a.py"])
    check("int symbols ignored (not iterated as digits)", led["contracts"]["f-scalar"]["symbols"], [])
    check("None tests ignored", led["contracts"]["f-scalar"]["tests"], [])
    check("empties stripped + deduped", led["contracts"]["f-empty"]["files"], ["b.py"])
    check("non-dict contract items skipped", len(led["contracts"]), 2)


def test_merge_tolerates_non_list_new_contracts():
    # PR#44 #1: a top-level non-list (e.g. SLM returned a dict or scalar) must not crash merge.
    led = {"version": 1, "contracts": {}}
    ml.merge_contracts(led, "garbage", turn=1)   # str is iterable-of-chars but each char is not a dict
    check("string new_contracts -> no contracts, no crash", led["contracts"], {})


def test_mentions_word_boundary():
    # PR#44 #5: substring matching over-fired. _mentions must be whole-token.
    check("'id' does not fire inside 'invalid'", ml._mentions("id", "an invalid value"), False)
    check("empty token never fires", ml._mentions("", "anything at all"), False)
    truthy("whole word fires", ml._mentions("timeout", "set the timeout now"))
    truthy("code symbol with underscore fires", ml._mentions("connect_timeout", "use connect_timeout here"))
    truthy("filename with dot fires", ml._mentions("a.py", "edit a.py please"))
    check("a.py does not fire inside data.py", ml._mentions("a.py", "edit data.py please"), False)


def test_guard_no_false_fire_from_substring():
    # the concrete over-fire the review flagged: a symbol 'id' must not match 'invalid' in an
    # unrelated prompt and drag its contract into the guard.
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "ident", "contract": "id field", "files": ["x.py"],
                        "symbols": ["id"]}])])
        raw = "Reject an invalid header in httpx/_status_codes.py."   # 'id' ⊂ 'invalid' but no real hit
        spec = _spec(target_files=["httpx/_status_codes.py"], scope="localized")
        check("substring 'id' in 'invalid' does NOT fire the guard",
              ml.refactor_guard_checklist(d, raw, spec), "")


def test_slm_accepts_top_level_array():
    # PR#44 #7: SLM may return a bare [ ... ] instead of {"contracts": [...]}.
    class ArrayJudge:
        def __call__(self, prompt, timeout=90):
            return json.dumps([{"feature": "f", "contract": "c", "files": ["a.py"]}]), 0.0, 0.0
    out, cost, ok = ml._slm_extract_contracts("raw", "", [], [], judge=ArrayJudge())
    truthy("top-level array accepted -> ok", ok)
    check("contracts parsed from bare array", out[0]["feature"], "f")


def test_slm_prose_is_failure_not_empty():
    # PR#44 #7: non-empty, non-JSON output (model refused / chatted) => ok=False, not silent empty.
    class ProseJudge:
        def __call__(self, prompt, timeout=90):
            return "Sorry, I cannot find any durable contracts in this turn.", 0.0, 0.0
    out, cost, ok = ml._slm_extract_contracts("raw", "", [], [], judge=ProseJudge())
    check("unparseable prose -> ok=False", ok, False)
    check("no phantom contracts", out, [])


def test_slm_wrong_shape_is_failure():
    # PR#44 #7: parseable JSON of the wrong shape (contracts not a list) => failure.
    class WrongShapeJudge:
        def __call__(self, prompt, timeout=90):
            return json.dumps({"contracts": "a string not a list"}), 0.0, 0.0
    _out, _c, ok = ml._slm_extract_contracts("raw", "", [], [], judge=WrongShapeJudge())
    check("wrong-shape contracts -> ok=False", ok, False)


def test_guard_output_is_capped():
    # PR#44 #4: the refactor guard must be bounded — the bounded-token claim depends on it.
    with tempfile.TemporaryDirectory() as d:
        big = [(t, [{"feature": "feat-{0}".format(t),
                     "contract": "obligation " + ("x" * 400),
                     "files": ["httpx/_client.py"],
                     "tests": ["tests/test_{0}.py".format(t)],
                     "symbols": ["sym_{0}".format(t)]}]) for t in range(1, ml.MAX_CONTRACTS + 1)]
        _seed(d, big)
        raw = "Refactor httpx/_client.py to unify everything."   # refactor + file overlap -> all hit
        spec = _spec(target_files=["httpx/_client.py"], scope="broad")
        cl = ml.refactor_guard_checklist(d, raw, spec)
        truthy("guard fired", bool(cl))
        truthy("guard output capped at GUARD_MAX_CHARS (+ omission note slack)",
               len(cl) <= ml.GUARD_MAX_CHARS + 120)
        truthy("omission note present when truncated", "omitted for length" in cl)


def test_state_summary_is_capped():
    # PR#44 #11: the always-on ProjectState header must be bounded too.
    with tempfile.TemporaryDirectory() as d:
        big = [(t, [{"feature": "feat-{0}".format(t),
                     "contract": "obligation " + ("y" * 300)}]) for t in range(1, ml.MAX_CONTRACTS + 1)]
        _seed(d, big)
        summ = ml.ledger_state_summary(d)
        truthy("state summary capped",
               len(summ) <= ml.STATE_SUMMARY_MAX_CHARS + 120)


def test_file_regex_broadened():
    # PR#44 #12: config/doc contracts must be detectable, version numbers must not.
    impacted = ml._impacted_files("bump the version in setup.cfg and pyproject.toml; see README.md",
                                  _spec())
    truthy("setup.cfg detected", "setup.cfg" in impacted)
    truthy("pyproject.toml detected", "pyproject.toml" in impacted)
    truthy("README.md detected", "README.md" in impacted)
    no_files = ml._impacted_files("upgrade to version 1.5 and 2.0", _spec())
    check("version numbers are not files", no_files, set())


if __name__ == "__main__":
    for t in (test_merge_upsert, test_recency_bound, test_update_ledger_with_fake_slm,
              test_run3_distance_independent, test_run4_retry_after_surfaced,
              test_no_false_fire_on_unrelated_turn, test_overlap_fires_without_keyword,
              test_is_refactor_signals, test_clear,
              test_merge_tolerates_scalar_and_empty_payloads,
              test_merge_tolerates_non_list_new_contracts, test_mentions_word_boundary,
              test_guard_no_false_fire_from_substring, test_slm_accepts_top_level_array,
              test_slm_prose_is_failure_not_empty, test_slm_wrong_shape_is_failure,
              test_guard_output_is_capped, test_state_summary_is_capped,
              test_file_regex_broadened):
        t()
    if _failures:
        print("FAIL ({0} assertion(s)):".format(len(_failures)))
        for m in _failures:
            print("  -", m)
        sys.exit(1)
    print("PASS: ledger merge/bound, SLM-path (mocked), distance-independent run3/run4 recall, "
          "no-false-fire, keyword-free overlap, clear, + PR#44 hardening (scalar/empty/non-list "
          "payloads, word-boundary matching, top-level-array, prose/wrong-shape => ok=False, "
          "capped guard/state output, broadened file regex).")
