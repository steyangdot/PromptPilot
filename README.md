# PromptPilot

> SLM-powered control plane for AI coding agents.

PromptPilot uses a small-model harness around Codex/Claude-style agents to route prompts, clarify ambiguity, compress noisy context, preserve constraints, and decide when to pass through unchanged. The SLM manages the workflow; the frontier model writes and debugs the code.

PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction.

**Headline cost pattern (hybrid mode):** pay API cents for the frequent small harness calls, route the expensive coding-agent calls to a subscription you already pay for. In one measured chain5 run the SLM layer cost ~$0.0085 of real API spend while the same agent work would cost ~$38 at per-token API rates. That's a **marginal** gap, not free work — the agent side consumes subscription quota (fixed monthly fee + finite ceiling that sustained runs *do* exhaust). See [docs/HYBRID_MODE.md](docs/HYBRID_MODE.md) and [docs/BENCHMARKS.md](docs/BENCHMARKS.md). Single workload, not a guarantee.

> **First-time user?** Start with **[QUICKSTART.md](QUICKSTART.md)**.

## Docs

Long-form documentation lives in [docs/](docs/) (source of truth) and is mirrored to the [GitHub Wiki](https://github.com/steyangdot/PromptPilot/wiki) by [scripts/publish_wiki.sh](scripts/publish_wiki.sh). Either is fine to read.

- Start at the [docs index](docs/README.md) or the [Project Overview](docs/PROJECT_OVERVIEW.md).
- Operational pages stay at the repo root: this README, [QUICKSTART.md](QUICKSTART.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

PromptPilot wraps an existing coding agent CLI — install one first:

- **Claude Code:** `npm install -g @anthropic-ai/claude-code`, then `claude auth login --claudeai`
- **Codex:** `npm install -g @openai/codex`, then `codex login`

Then install PromptPilot with the matching extra:

```bash
pip install prpt[claude]      # for use with claude-code (Claude Haiku SLM)
pip install prpt[codex]       # for use with codex (GPT-5.4-nano SLM)
pip install prpt[all]         # both
```

`[anthropic]` / `[openai]` are kept as aliases for backward compatibility.

## First run

```bash
prpt setup                                # one-time onboarding (checks + smoke test)
prpt "fix the flaky test in payments"     # auto-detects claude or codex from PATH
prpt --dry-run "refactor auth, no API changes"  # preview the optimized prompt
prpt --tool codex "add dark mode"         # force a specific agent
prpt doctor                               # re-run setup checks if something breaks
```

After many turns the session grows heavy:

```bash
prpt restart                              # checkpoint -> handoff.md -> bootstrap fresh
```

## What a run looks like

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

The SLM expanded the raw 248-token prompt into a 332-token optimized version that pinned the failing test name and made the `TokenStore` API-stability constraint explicit before the coding agent saw it. Walkthrough in [docs/TELEMETRY_AND_REPLAY.md](docs/TELEMETRY_AND_REPLAY.md).

For the full guide see **[QUICKSTART.md](QUICKSTART.md)** and `prpt --help`
(or `prpt --advanced-help` for internal/researcher flags).
