# Examples

![PromptPilot demo](../docs/assets/demo.svg)

## Runnable demo — [`demo.py`](demo.py)

See PromptPilot's SLM control layer reshape a raw prompt, with **zero setup** —
no API key, no coding-agent install, no network:

```bash
python examples/demo.py            # offline heuristic — runs anywhere
python examples/demo.py --slm      # live small-model rewrite (needs an API key or subscription)
python examples/demo.py --offline  # force the offline path (never touches the network)
python examples/demo.py --only 2   # run just one of the examples
python examples/demo.py --full     # print the full structured prompt for every example
python examples/demo.py --ascii    # ASCII-only box glyphs for limited terminals
```

By default the demo uses the rule-based heuristic normalizer so anyone (and CI)
can run it. If an SLM backend is detected (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
in the environment) it auto-upgrades to the real small-model rewrite; `--slm`
forces it (including for Max / ChatGPT subscriptions). Every field shown —
route, task type, confidence, protected spans, hard constraints, ambiguities —
is computed by PromptPilot's real pipeline against a fixed synthetic repo, so
the output is deterministic and reproducible wherever you run it.

The three built-in examples walk from a precise request (constraints pinned) to
an under-specified one (ambiguities flagged) to a mixed refactor (hard vs soft
constraints separated).

<details>
<summary><b>Sample output</b> (offline heuristic, example 1 of 3 — abridged; run it for the full output)</summary>

```text
  RAW PROMPT
    the OrderSyncWorker keeps timing out under load and dropping events.
    find the root cause and fix the timeout. do not change the public API,
    keep the DB schema backward compatible, and ship a minimal patch with
    a regression test in tests/test_worker.py.

  PROMPTPILOT  route=act  task=root_cause_analysis  confidence=high  needs_review=no

  EXTRACTED
    protected spans   minimal patch
                      backward compatible
                      root cause
                      OrderSyncWorker
                      API
                      DB
                      tests/test_worker.py
    hard constraints  fix the timeout. do not change the public API
                      keep the DB schema backward compatible
                      ship a minimal patch with a regression test in
                      tests/test_worker.py.
    requested output  dropping events. find the root cause
    ambiguities       (none)
    assumptions       Recently changed files may be relevant to the requested task.

  STRUCTURED PROMPT  -> forwarded to the coding agent
    +-----------------------------------------------------------------------+
    | Original user request:                                                |
    | the OrderSyncWorker keeps timing out under load and dropping events.  |
    | find the root cause and fix the timeout. do not change the public API,|
    | keep the DB schema backward compatible, and ship a minimal patch with |
    | a regression test in tests/test_worker.py.                            |
    | ...                                                                   |
    | - Prefer the smallest safe change when the request is underspecified. |
    +-----------------------------------------------------------------------+
```

The offline run uses the rule-based heuristic so it works for everyone (note the
slightly rough extraction); `--slm` swaps in the real small model for a sharper
rewrite.

</details>

<details>
<summary><b>What a live run looks like</b> (real SLM path, with token stats)</summary>

```text
$ prpt "the test in tests/test_auth.py::test_token_refresh is flaky on CI
        but passes locally. keep the public API of TokenStore intact."
[promptpilot] session: carrying 0 prior turns
[promptpilot] route=act
[token stats] raw 248 → optimized 332 tokens (SLM call: $0.0021)
=== forwarding to claude-code ===
... agent works ...
✓ tests/test_auth.py::test_token_refresh now stable (3/3 CI retries)
```

The SLM expanded the raw 248-token prompt into a 332-token version that pinned
the failing test name and made the `TokenStore` API-stability constraint
explicit before the coding agent saw it. Walkthrough:
[docs/TELEMETRY_AND_REPLAY.md](../docs/TELEMETRY_AND_REPLAY.md).

</details>

## Worked-example categories

These directories describe the behaviors the demo illustrates, with guidance for
building your own fixtures:

- [`passthrough/`](passthrough/) — inputs where rewriting is risky, so the raw
  prompt is forwarded unchanged.
- [`semantic-preservation/`](semantic-preservation/) — prompts where explicit
  constraints, file paths, and symbols must survive any rewrite.
- [`tool-output-compression/`](tool-output-compression/) — noisy tool output
  (pytest / grep / diff) compressed before the coding agent sees it.

## Related

- [README.md](../README.md) — project overview and architecture.
- [QUICKSTART.md](../QUICKSTART.md) — five-minute onboarding for the `prpt` CLI.
- [docs/RECORDING.md](../docs/RECORDING.md) — how the demo asset (`docs/assets/demo.svg`) is produced.
