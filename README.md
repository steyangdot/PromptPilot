# PromptPilot

PromptPilot uses a small language model harness around Codex/Claude-style agents to route prompts, clarify ambiguity, compress noisy context, preserve constraints, and decide when to pass through unchanged. The SLM manages the workflow; the frontier model writes and debugs the code.

PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction.

> **First-time user?** Start with **[QUICKSTART.md](QUICKSTART.md)**.

## Docs strategy (lean repo + GitHub Wiki)

To keep this repository lightweight, long-form product/positioning documentation now lives in the **GitHub Wiki**.

- **Wiki home:** <https://github.com/steyangdot/PromptPilot/wiki>
- In-repo docs are kept minimal and operational (quickstart, security, contributing).
- Publish wiki pages from repo docs: `scripts/publish_wiki.sh` (details in `docs/WIKI_WORKFLOW.md`).

## What the SLM does — and does not do

PromptPilot uses a small language model as the harness around the coding agent.

The SLM does:

- Classify user intent
- Detect ambiguity
- Decide whether to clarify, answer, pass through, or invoke the coding agent
- Rewrite prompts when safe
- Compress noisy tool output
- Preserve explicit constraints, file paths, failing tests, and stack traces
- Recommend passthrough when transformation risk is high

The SLM does not:

- Replace the coding model
- Make deep implementation decisions
- Debug complex code by itself
- Modify files directly
- Hide or discard high-risk context to save tokens

The frontier coding agent still performs the hard reasoning, debugging, implementation, and test-fixing work.

## Project docs

- [Architecture](docs/ARCHITECTURE.md) — how the SLM harness, routes, coding agent, and telemetry fit together.
- [SLM Harness](docs/SLM_HARNESS.md) — what the small model is trusted to do and where it must fall back.
- [Semantic Preservation](docs/SEMANTIC_PRESERVATION.md) — why shorter output is not success unless critical facts survive.
- [Safety Model](docs/SAFETY_MODEL.md) — bounded trust and passthrough rules.
- [Benchmarks](docs/BENCHMARKS.md) — efficiency plus preservation framing.
- [Comparison](docs/COMPARISON.md) — how PromptPilot differs from token reducers, routers, orchestrators, and coding agents.
- [Roadmap](docs/ROADMAP.md) — planned work for bounded SLM control-layer improvements.
- [FAQ](docs/FAQ.md) — answers to common positioning questions.

## Install

Pick the extra that matches your coding agent:

```bash
pip install prpt[claude]      # for use with claude-code (Claude Haiku SLM)
pip install prpt[codex]       # for use with codex (GPT-5.4-nano SLM)
pip install prpt[all]         # both
```

`[anthropic]` / `[openai]` are kept as aliases for backward compatibility.
# PromptPilot

> An SLM-powered control plane for AI coding agents.

PromptPilot sits between developers and Codex/Claude-style coding agents. It uses a small language model as the harness layer to decide what should happen before the expensive agent runs: clarify, answer directly, compress noisy context, rewrite safely, pass through unchanged, or invoke the full coding agent.

The goal is not to replace frontier coding models. The goal is to make them cheaper, safer, and easier to debug by controlling the context and workflow around them.

PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction.

**The SLM manages the workflow; the frontier model writes the code.**

> **First-time user?** Start with **[QUICKSTART.md](QUICKSTART.md)**.

## Docs strategy (lean repo + GitHub Wiki)

To keep this repository lightweight, long-form product/positioning documentation now lives in the **GitHub Wiki**.

- **Wiki home:** <https://github.com/steyangdot/PromptPilot/wiki>
- In-repo docs are kept minimal and operational (quickstart, security, contributing).
- Publish wiki pages from repo docs: `scripts/publish_wiki.sh` (details in `docs/WIKI_WORKFLOW.md`).
