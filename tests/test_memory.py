"""pytest suite for prpt.memory — the ProjectState contract ledger + additive-bias refactor guard
(ported from research/memory_ledger.py into the product). Pure stdlib + an injected FakeJudge (no
network, no real SLM). The load-bearing test (test_run3_distance_independent) replays the N=5 run3
failure: a contract from turn 1, buried under 4 later contracts, is STILL surfaced at a turn-13
refactor — which the MAX_TURNS=4 recency window cannot do.
"""
import json
import os
import tempfile
import types

import prpt.memory as ml


def check(name, got, want):
    assert got == want, "{0}: got {1!r}, want {2!r}".format(name, got, want)


def truthy(name, cond):
    assert cond, "{0}: expected truthy, got {1!r}".format(name, cond)


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


def test_cli_memory_ledger_injects_guard_recency_does_not():
    """End-to-end wiring: `prpt --memory ledger` injects the prior contracts + additive-bias guard
    into the downstream prompt; the recency default does not (opt-in, off by default)."""
    import io
    import contextlib
    from prpt.cli import main

    with tempfile.TemporaryDirectory() as d:
        led = ml.load_ledger(d)
        ml.merge_contracts(led, [{"feature": "timeout-overrides",
                                  "contract": "Client accepts a per-request connect_timeout",
                                  "files": ["x.py"], "symbols": ["connect_timeout"]}], turn=1)
        ml.save_ledger(d, led)

        def run(extra):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["refactor the connect_timeout handling into a ResilienceConfig object",
                           "--dry-run", "--normalizer", "heuristic", "--cwd", d,
                           "--no-repo-context", *extra])
            return rc, buf.getvalue()

        rc_ledger, out_ledger = run(["--memory", "ledger"])
        rc_recency, out_recency = run([])

        assert rc_ledger == 0
        assert "PREFER ADDITIVE" in out_ledger          # additive-bias guard injected downstream
        assert "connect_timeout" in out_ledger          # the prior contract surfaced
        assert "PREFER ADDITIVE" not in out_recency      # recency default unchanged (opt-in only)


# --- PR#50 review fixes: lifecycle (clear on every reset) + write-gating -----
def test_reset_ledger_if_cleared_helper():
    """PR#50 review (P2): every session-reset path must drop the ledger sidecar so a later
    `--memory ledger` run can't inject stale contracts. The helper clears iff `cleared`."""
    from prpt.cli import _reset_ledger_if_cleared
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "x", "contract": "c", "files": ["a.py"]}])])
        truthy("sidecar exists before reset", ml._ledger_path(d).exists())
        _reset_ledger_if_cleared(d, False)   # not cleared -> ledger untouched
        truthy("not-cleared leaves the ledger intact", ml._ledger_path(d).exists())
        _reset_ledger_if_cleared(d, True)    # cleared -> ledger dropped
        check("cleared drops the sidecar", ml._ledger_path(d).exists(), False)


class _FakeAdapter:
    """No-network stand-in for a downstream adapter: returns a fixed exit code + modified list."""
    def __init__(self, rc, modified):
        self._rc = rc
        self.last_modified_files = modified
        self.last_usage = None

    def run(self, final_prompt, args):
        return self._rc


class _FakeFactory:
    def __init__(self, rc, modified):
        self._rc, self._modified = rc, modified

    def create(self, args):
        return _FakeAdapter(self._rc, self._modified)


def test_ledger_update_gated_on_success_and_edits(monkeypatch):
    """PR#50 review (P2): only persist contracts after a SUCCESSFUL run with real edits. A failed
    run (nonzero exit -- incl. a failed verify gate, which is folded into exit_code) or a no-edit
    run must NOT record obligations for APIs that never landed."""
    import prpt.cli as cli
    calls = []

    def _rec(cwd, raw, spec, modified, turn=None, judge=None, **kw):
        calls.append((tuple(modified), turn))
        return ({"version": 1, "contracts": {}}, 0.0, True)

    monkeypatch.setattr(cli.memory, "update_ledger", _rec)
    monkeypatch.setattr(cli.memory, "ledger_judge_available", lambda: True)  # judge present: gate is exit/edits only

    def _run(rc, modified):
        calls.clear()
        monkeypatch.setattr(cli, "AdapterFactory", _FakeFactory(rc, modified))
        with tempfile.TemporaryDirectory() as d:
            cli.main(["edit httpx/_client.py to change the connect_timeout handling",
                      "--normalizer", "heuristic", "--cwd", d, "--no-repo-context",
                      "--memory", "ledger"])
        return list(calls)

    truthy("success + real edits -> ledger updated", _run(0, ["httpx/_client.py"]))
    check("nonzero exit -> ledger NOT updated", _run(1, ["httpx/_client.py"]), [])
    check("success but no edits -> ledger NOT updated", _run(0, []), [])


def test_ledger_respects_session_ttl():
    """PR#50 review (P2): the ledger must honor the same idle-expiry as the recency session, so
    `--memory ledger` can't resurrect contracts from a session load_recent_turns would already skip."""
    import prpt.session as sess
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "x", "contract": "c", "files": ["a.py"]}])])
        truthy("fresh ledger is loaded", "x" in ml.load_ledger(d)["contracts"])
        # backdate updated_at past the TTL via a direct file write (NOT save_ledger, which re-stamps)
        p = ml._ledger_path(d)
        raw = json.loads(p.read_text(encoding="utf-8"))
        truthy("save_ledger stamped updated_at", "updated_at" in raw)
        raw["updated_at"] = raw["updated_at"] - sess.SESSION_TTL - 100
        p.write_text(json.dumps(raw), encoding="utf-8")
        check("stale ledger ignored (idle past SESSION_TTL)", ml.load_ledger(d)["contracts"], {})


def test_ledger_warnings_go_to_stderr(capsys):
    """PR#50 review (P3): degradation warnings must go to stderr, never pollute stdout in automation."""
    class EmptyJudge:
        def __call__(self, prompt, timeout=90):
            return "", 0.0, 0.0
    with tempfile.TemporaryDirectory() as d:
        ml.update_ledger(d, "x", _spec(), [], turn=1, judge=EmptyJudge())   # ok=False -> warns
        cap = capsys.readouterr()
        truthy("degradation warning emitted on stderr", "WARNING" in cap.err)
        check("nothing leaked to stdout", "WARNING" in cap.out, False)


# --- PR#50 /code-review fixes (xhigh) ---------------------------------------
def test_save_ledger_atomic_and_no_mutation():
    """Review (P2): save_ledger writes atomically (temp + os.replace, no leftover .tmp) and stamps
    updated_at on a COPY so the caller's dict is not mutated."""
    with tempfile.TemporaryDirectory() as d:
        led = {"version": 1, "contracts": {"x": {"feature": "x", "contract": "c", "turn": 1}}}
        truthy("save reported ok", ml.save_ledger(d, led))
        check("caller dict NOT mutated in place", "updated_at" in led, False)
        on_disk = json.loads(ml._ledger_path(d).read_text(encoding="utf-8"))
        truthy("persisted copy carries updated_at", "updated_at" in on_disk)
        p = ml._ledger_path(d)
        check("no temp file left behind", p.with_suffix(p.suffix + ".tmp").exists(), False)


def test_mentions_no_overfire_across_underscore_or_hyphen():
    """Review (P2): a short token must not fire INSIDE a larger underscored/hyphenated identifier."""
    check("'timeout' not inside 'read_timeout'", ml._mentions("timeout", "add a read_timeout knob"), False)
    check("'retry-after' not inside 'retry-after-cap'", ml._mentions("retry-after", "set retry-after-cap now"), False)
    check("'after' not inside 'retry-after'", ml._mentions("after", "the retry-after header"), False)
    truthy("whole token still fires", ml._mentions("timeout", "set the timeout now"))
    truthy("underscored whole token still fires", ml._mentions("connect_timeout", "use connect_timeout here"))


def test_memory_env_value_normalized_and_validated(monkeypatch):
    """Review (correctness): the env-sourced --memory default is normalized + validated (argparse only
    validates explicit CLI args), so wrong-case works and a typo falls back to recency loudly."""
    import prpt.cli as cli
    monkeypatch.setenv("PROMPTPILOT_MEMORY", "LEDGER")
    check("wrong-case env normalized to 'ledger'", cli.parse_args(["edit x"]).memory, "ledger")
    monkeypatch.setenv("PROMPTPILOT_MEMORY", "ledgr")
    check("typo'd env falls back to 'recency'", cli.parse_args(["edit x"]).memory, "recency")
    monkeypatch.delenv("PROMPTPILOT_MEMORY", raising=False)
    check("explicit --memory ledger honored", cli.parse_args(["edit x", "--memory", "ledger"]).memory, "ledger")
    check("default is recency", cli.parse_args(["edit x"]).memory, "recency")


def test_slm_extract_fail_soft_on_extract_json_exception(monkeypatch):
    """Review (low): extract_json raising (e.g. RecursionError on pathological output) must degrade to
    ([], cost, False), not propagate — honoring the documented fail-soft contract."""
    import prpt.judges as judges

    def _boom(_text):
        raise RecursionError("nested too deep")
    monkeypatch.setattr(judges, "extract_json", _boom)

    class NonEmptyJudge:
        def __call__(self, prompt, timeout=90):
            return "[{not really parsed}]", 0.001, 0.0
    out, cost, ok = ml._slm_extract_contracts("raw", "", [], [], judge=NonEmptyJudge())
    check("fail-soft on parser exception (no raise)", (out, ok), ([], False))
    check("cost preserved across the failure", round(cost, 3), 0.001)


def test_ledger_update_skipped_without_judge(monkeypatch, capsys):
    """Review (P2): with no judge, the after-turn update is skipped (no per-turn 'degrading' spam) and
    one upfront warning is emitted; guard injection is independent and still works."""
    import prpt.cli as cli
    calls = []

    def _rec(*a, **k):
        calls.append(a)
        return ({"version": 1, "contracts": {}}, 0.0, True)
    monkeypatch.setattr(cli.memory, "update_ledger", _rec)
    monkeypatch.setattr(cli.memory, "ledger_judge_available", lambda: False)
    monkeypatch.setattr(cli, "AdapterFactory", _FakeFactory(0, ["httpx/_client.py"]))
    with tempfile.TemporaryDirectory() as d:
        cli.main(["edit httpx/_client.py", "--normalizer", "heuristic", "--cwd", d,
                  "--no-repo-context", "--memory", "ledger"])
    err = capsys.readouterr().err
    check("update_ledger NOT called when no judge", calls, [])
    truthy("one upfront 'needs an SLM judge' warning", "needs an SLM judge" in err)


# --- Stage A (docs/SESSION_MEMORY_V2_DESIGN.md §6) ---------------------------
def test_removal_intent_is_user_conditioned():
    c = {"feature": "retry-after", "contract": "Retry-After parsed + capped",
         "files": ["httpx/_transports/default.py"], "symbols": ["parse_http_date"]}
    truthy("kw + symbol anchor fires", ml.detect_removal_intent(
        "remove the parse_http_date helper entirely", c))
    truthy("kw + feature-as-words fires", ml.detect_removal_intent(
        "please drop the retry after handling", c))
    truthy("phrase kw fires", ml.detect_removal_intent(
        "clean up the retry-after code path", c))
    check("kw without any anchor does NOT fire", ml.detect_removal_intent(
        "remove the stale docs paragraph", c), False)
    check("anchor without removal kw does NOT fire", ml.detect_removal_intent(
        "improve parse_http_date performance", c), False)


def test_tombstone_quarantine_lifecycle():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [(1, [{"feature": "retry-after", "contract": "Retry-After parsed",
                        "files": ["httpx/_transports/default.py"],
                        "tests": ["tests/test_retries.py"], "symbols": ["retry_after"]}])])
        led = ml.load_ledger(d)
        # turn 5: the USER asks to remove it -> quarantined, not tombstoned
        marked = ml.propose_deprecations(led, "remove the retry-after handling", turn=5)
        check("quarantined", marked, ["retry-after"])
        check("status deprecating", led["contracts"]["retry-after"]["status"], "deprecating")
        # guard STILL fires during quarantine
        hits = ml.guard_hits(led, "refactor the retry_after path", _spec(scope="broad"))
        truthy("guard fires for quarantined contract", "retry-after" in hits)
        # same turn's green cannot finalize (one-turn quarantine)
        ml.finalize_deprecations(led, gate_green=True, turn=5)
        check("same-turn green does NOT finalize", led["contracts"]["retry-after"]["status"], "deprecating")
        # a later red turn does not finalize either
        ml.finalize_deprecations(led, gate_green=False, turn=6)
        check("red turn holds quarantine", led["contracts"]["retry-after"]["status"], "deprecating")
        # deleted locking tests veto the finalize even on green
        ml.finalize_deprecations(led, gate_green=True, turn=6, tests_deleted=["tests/test_retries.py"])
        check("deleted locking tests veto", led["contracts"]["retry-after"]["status"], "deprecating")
        # a later green turn finalizes
        done = ml.finalize_deprecations(led, gate_green=True, turn=6)
        check("green next turn tombstones", done, ["retry-after"])
        # tombstoned: excluded from guard + state; rendered in the retired section
        check("guard no longer fires", ml.guard_hits(led, "refactor the retry_after path",
                                                     _spec(scope="broad")), {})
        ml.save_ledger(d, led)
        truthy("state summary excludes tombstone", "retry-after" not in ml.ledger_state_summary(d))
        prefix = ml.memory_prefix(d, "refactor everything", _spec(scope="broad"))
        truthy("retired section names it", "RETIRED" in prefix and "retry-after" in prefix)
        truthy("do-not-resurrect instruction present", "resurrect" in prefix)


def test_tombstones_never_displace_active_contracts():
    # panel A10 / design §6-3: the retired list has its OWN cap; actives keep theirs.
    with tempfile.TemporaryDirectory() as d:
        seeds = [(t, [{"feature": "feat-{0}".format(t), "contract": "obligation " + "x" * 80}])
                 for t in range(1, ml.MAX_CONTRACTS + 1)]
        _seed(d, seeds)
        led = ml.load_ledger(d)
        for t in range(1, 11):    # tombstone 10 of them
            led["contracts"]["feat-{0}".format(t)]["status"] = "tombstone"
            led["contracts"]["feat-{0}".format(t)]["tombstone_turn"] = t
        ml.save_ledger(d, led)
        summ = ml.ledger_state_summary(d)
        tomb = ml._tombstone_section(ml.load_ledger(d)["contracts"])
        truthy("active summary still capped", len(summ) <= ml.STATE_SUMMARY_MAX_CHARS + 120)
        truthy("tombstone section separately capped", len(tomb) <= ml.TOMBSTONE_MAX_CHARS + 120)
        truthy("the FIRST active contract is present in the summary", "feat-11" in summ)
        check("tombstoned contract absent from active summary", "feat-1:" in summ, False)


def test_wip_overwrite_and_unverified_render():
    with tempfile.TemporaryDirectory() as d:
        class WipJudge:
            def __init__(self, wip): self.wip = wip
            def __call__(self, prompt, timeout=90):
                return json.dumps({"contracts": [], "wip": self.wip}), 0.0, 0.0
        ml.update_ledger(d, "start the feature", _spec(), ["a.py"], turn=1,
                         judge=WipJudge("async variant still missing"))
        check("wip stored", ml.load_ledger(d)["wip"]["text"], "async variant still missing")
        prefix = ml.memory_prefix(d, "continue", _spec())
        truthy("wip rendered as UNVERIFIED narrative", "unverified" in prefix and "WIP" in prefix)
        # overwrite-not-merge: next turn's empty wip CLEARS it (cannot accrete)
        ml.update_ledger(d, "finish it", _spec(), ["a.py"], turn=2, judge=WipJudge(""))
        check("empty wip clears the note", "wip" in ml.load_ledger(d), False)
        # oversized wip is truncated to the cap
        ml.update_ledger(d, "x", _spec(), ["a.py"], turn=3, judge=WipJudge("y" * 1000))
        truthy("wip truncated to cap", len(ml.load_ledger(d)["wip"]["text"]) <= ml.WIP_MAX_CHARS)


def test_done_not_asked_prompt_carries_gate_verdict():
    seen = {}

    class RecordingJudge:
        def __call__(self, prompt, timeout=90):
            seen["prompt"] = prompt
            return json.dumps({"contracts": []}), 0.0, 0.0
    with tempfile.TemporaryDirectory() as d:
        ml.update_ledger(d, "add a knob", _spec(), ["a.py"], turn=1,
                         judge=RecordingJudge(), gate_verdict="green")
    truthy("gate verdict in extraction prompt", "[Gate verdict]" in seen["prompt"]
           and "green" in seen["prompt"])
    truthy("DONE-not-ASKED instruction present", "actually DID" in seen["prompt"])


def test_guard_test_targets_and_unlocked():
    with tempfile.TemporaryDirectory() as d:
        _seed(d, [
            (1, [{"feature": "timeout-overrides", "contract": "timeout kwargs work",
                  "files": ["httpx/_client.py"], "tests": ["tests/client/test_client.py"],
                  "symbols": ["connect_timeout"]}]),
            (2, [{"feature": "pool-size", "contract": "pool_size wired",
                  "files": ["httpx/_client.py"], "symbols": ["pool_size"]}]),   # NO tests
        ])
        out = ml.guard_test_targets(d, "refactor httpx/_client.py timeouts and pool_size",
                                    _spec(target_files=["httpx/_client.py"], scope="broad"))
        check("locked tests targeted", out["targets"], ["tests/client/test_client.py"])
        check("no-test contract reported UNLOCKED", out["unlocked"], ["pool-size"])
        truthy("both contracts hit", set(out["hits"]) == {"timeout-overrides", "pool-size"})
        # the guard text tells the agent about the unverifiable obligation
        cl = ml.refactor_guard_checklist(d, "refactor httpx/_client.py pool_size handling",
                                         _spec(target_files=["httpx/_client.py"], scope="broad"))
        truthy("UNLOCKED note rendered", "UNLOCKED" in cl)
        # tombstoned contracts contribute no targets
        led = ml.load_ledger(d)
        led["contracts"]["timeout-overrides"]["status"] = "tombstone"
        ml.save_ledger(d, led)
        out2 = ml.guard_test_targets(d, "refactor httpx/_client.py timeouts",
                                     _spec(target_files=["httpx/_client.py"], scope="broad"))
        check("tombstone contributes no targets", out2["targets"], [])


# (standalone __main__ runner removed — pytest discovers the test_* functions; check/truthy assert)
