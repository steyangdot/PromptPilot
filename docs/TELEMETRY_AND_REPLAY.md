# Telemetry and Replay

PromptPilot records enough information to make harness behavior inspectable.

Telemetry should help answer:

- Which route did the harness choose?
- Why was a prompt clarified, rewritten, compressed, or passed through?
- How many tokens were saved?
- Were critical facts preserved?
- What changed across a session restart or handoff?

## Walkthrough: one session from first prompt to handoff

This is an illustrative trace of what a `prpt` session looks like end-to-end. Numbers reflect Haiku 4.5 SDK pricing; your exact cost depends on the backend.

```text
$ prpt "the test in tests/test_auth.py::test_token_refresh is flaky on CI but
        passes locally. keep the public API of TokenStore intact."
[promptpilot] session: carrying 0 prior turns (run `prpt new-session` to clear)
[promptpilot] route=act
[token stats] raw 248 tokens → optimized 332 tokens (+34%, +$0.0004 vs raw)
              SLM call: 1,847 in / 264 out → $0.0021
              estimated downstream saving: $0.0140 (cleaner prompt → fewer agent steps)
=== forwarding to claude-code ===
... agent works ...
✓ tests/test_auth.py::test_token_refresh now stable (3/3 CI retries)
[token stats] actual downstream: 12.4k in / 2.1k out → $0.115
```

The optimized prompt added the explicit `TokenStore` API-stability constraint and pinned the failing test name, both of which the SLM extracted from the raw prompt. The 34% token expansion on the front end paid for itself by trimming agent backtracking — see the actual-vs-estimated downstream comparison.

Follow-up turns reuse the session memory:

```text
$ prpt "now add a regression test for the retry-window edge case"
[promptpilot] session: carrying 2 prior turns
[promptpilot] route=act
[token stats] raw 78 tokens → optimized 410 tokens (memory + extracted constraints)
```

When the session grows heavy, collapse it:

```text
$ prpt restart
Restarted: snapshot to handoff.md (4 turns, $0.0083, 6.2s) and bootstrapped fresh
          (user 421c, assistant 318c)
```

`handoff.md` now contains the Goal / Decisions / Files / Open items / Constraints summary — auditable, hand-editable, and reusable as the starting context for the next session.

## Common commands

Review recent run summaries:

```bash
prpt stats --last 10
```

Create a handoff snapshot:

```bash
prpt checkpoint
```

Restart with a compact summary:

```bash
prpt restart
```

Bootstrap from a curated handoff:

```bash
prpt bootstrap
```

## Handoff contents

A valid handoff should keep:

- Goal
- Decisions made
- Files touched
- Open items
- Constraints

These sections make restarts auditable and reduce the chance that important context disappears.

---

**See also:** [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart) · [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression) · [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks)
