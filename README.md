# PromptPilot

> Small-language-model control layer for AI coding agents.

**PromptPilot puts a cheap small model in front of Codex and Claude Code: it turns a rough prompt into a clear, constraint-pinned brief — so the frontier model stops burning tokens on ambiguity, repeated history, and noisy tool output.**

![PromptPilot demo](docs/assets/demo.svg)

**Try it in one command — no API key, no coding agent, no network:**

```bash
python examples/demo.py          # offline: watch a rough prompt become a structured brief
```

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

The offline run uses the rule-based heuristic so it works for everyone (note
the slightly rough extraction); `--slm` swaps in the real small model for a
sharper rewrite. See [examples/](examples/) for the runnable demo and the
worked-example categories.

</details>

With an API key or subscription, `python examples/demo.py --slm` shows the live small-model rewrite. **New here? → [QUICKSTART.md](QUICKSTART.md).**

---

## What it does

- **Clarifies vague requests** — flags ambiguity and asks first instead of guessing.
- **Pins constraints & protected spans** — APIs, file paths, "don't touch X" go into the prompt explicitly.
- **Routes every request** — *clarify / answer / passthrough / act* — instead of blindly forwarding.
- **Bounds session memory** — long sessions don't re-feed the whole transcript every turn.
- **Compresses noisy tool output** — pytest / grep / diff, via agent hooks, before the expensive model reads it.

The SLM manages the workflow; the frontier model still writes and debugs the code. PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction — a rewrite may be *longer* when that preserves a constraint. The savings come from fewer ambiguous turns, bounded replay, and compressed context.

> **Measured (hybrid mode, one 15-turn chain):** ~24k input tokens of SLM work directed ~12.66M input tokens of agent work — the control layer was **~0.2%** of the input footprint, and the bounded session ran the same work on **~7.6× fewer** input tokens than the tool's native `--resume`. Single workload, not a guarantee — see [Benchmarks](docs/BENCHMARKS.md) and [Hybrid Mode](docs/HYBRID_MODE.md).

## Install

PromptPilot wraps an existing coding-agent CLI — install and authenticate at least one first:

- **Claude Code:** `npm install -g @anthropic-ai/claude-code` → `claude auth login --claudeai`
- **Codex:** `npm install -g @openai/codex` → `codex login`

```bash
pip install prpt[claude]      # Claude/Anthropic SLM path
pip install prpt[codex]       # Codex/OpenAI SLM path
pip install prpt[all]         # both
```

Subscription auth and API keys both work; **hybrid mode** can route the small control layer to a metered API key and the coding agent to a subscription CLI. (`[anthropic]` / `[openai]` remain as aliases.)

## First run

```bash
cd /path/to/your/repo
prpt setup                                # one-time onboarding (checks + smoke test)
prpt "fix the flaky test in payments"     # auto-detects claude or codex from PATH
prpt --dry-run "refactor auth, no API changes"  # preview the optimized prompt
prpt --tool codex "add dark mode"         # force a specific agent
prpt restart                              # collapse a heavy session -> handoff.md -> fresh
```

`prpt doctor` re-runs setup checks; `prpt install-hook` wires prompt/tool hooks into Claude Code. Full flag set: `prpt --help` (or `prpt --advanced-help` for researcher/internal flags).

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

The SLM expanded the raw 248-token prompt into a 332-token version that pinned the failing test name and made the `TokenStore` API-stability constraint explicit before the coding agent saw it. Walkthrough: [docs/TELEMETRY_AND_REPLAY.md](docs/TELEMETRY_AND_REPLAY.md).

</details>

## How it works

<details>
<summary><b>Architecture</b> — control plane → coding agent → optional hooks</summary>

```mermaid
%%{init: {"flowchart": {"curve": "basis", "nodeSpacing": 48, "rankSpacing": 60}}}%%
flowchart LR
  U([Developer request])

  subgraph PP["PromptPilot control plane"]
    direction LR
    M[["Session memory<br/>bounded summaries"]]
    C{{"SLM route<br/>clarify / answer / passthrough / act"}}
    Q["Clarify<br/>ask first"]
    A["Answer<br/>offer reply"]
    D["Direct reply<br/>opt-in only"]
    P["Passthrough<br/>raw prompt"]
    R["Act<br/>safe rewrite"]
  end

  subgraph AG["Frontier coding agent"]
    direction LR
    F["Codex / Claude CLI"]
    O["Code changes<br/>tests / summary"]
    T["Tool output"]
  end

  subgraph HK["Optional hooks"]
    H["Compress logs<br/>pytest / grep / diff"]
  end

  U --> M --> C
  C -->|clarify| Q
  C -->|answer| A
  A -->|enabled| D
  A -.->|otherwise| F
  C -->|passthrough| P --> F
  C -->|act| R --> F
  F --> O
  F --> T --> H --> F

  C -. "hybrid" .-> API[("Metered SLM API")]
  F -. "hybrid" .-> SUB[("Subscription CLI")]

  classDef entry fill:#fff7ed,stroke:#fb923c,stroke-width:2px,color:#7c2d12;
  classDef control fill:#eef2ff,stroke:#6366f1,stroke-width:2px,color:#312e81;
  classDef route fill:#f5f3ff,stroke:#8b5cf6,stroke-width:2px,color:#4c1d95;
  classDef agent fill:#ecfeff,stroke:#06b6d4,stroke-width:2px,color:#164e63;
  classDef hook fill:#f0fdf4,stroke:#22c55e,stroke-width:2px,color:#14532d;
  classDef infra fill:#f8fafc,stroke:#94a3b8,stroke-width:1.5px,color:#334155;

  class U entry;
  class M,Q,A,D,P,R control;
  class C route;
  class F,O,T agent;
  class H hook;
  class API,SUB infra;
```

For `answer`, PromptPilot skips the downstream coding agent only when direct SLM answering is enabled (`--let-slm-answer` or `PROMPTPILOT_LET_SLM_ANSWER`); otherwise the request continues to the agent.

</details>

Dig deeper in [Architecture](docs/ARCHITECTURE.md), [Routes and Decisions](docs/ROUTES_AND_DECISIONS.md), and [Semantic Preservation](docs/SEMANTIC_PRESERVATION.md).

## Docs

Long-form docs live in [docs/](docs/) (source of truth), mirrored to the **[PromptPilot GitHub Wiki](https://github.com/steyangdot/PromptPilot/wiki)** by [scripts/publish_wiki.sh](scripts/publish_wiki.sh). Start at the [Project Overview](docs/PROJECT_OVERVIEW.md) or the [docs index](docs/README.md). Operational pages stay at the repo root: [QUICKSTART.md](QUICKSTART.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md).
