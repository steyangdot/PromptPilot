# PromptPilot

> SLM-powered control plane for AI coding agents.

PromptPilot uses a small-model harness around Codex/Claude-style agents to route prompts, clarify ambiguity, compress noisy context, preserve constraints, and decide when to pass through unchanged. The SLM manages the workflow; the frontier model writes and debugs the code.

PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction.

> **First-time user?** Start with **[QUICKSTART.md](QUICKSTART.md)**.

## Docs strategy (lean repo + GitHub Wiki)

To keep this repository lightweight, long-form product/positioning documentation now lives in the **GitHub Wiki**.

- **Wiki home:** <https://github.com/steyangdot/PromptPilot/wiki>
- Read the conceptual overview in the wiki: <https://github.com/steyangdot/PromptPilot/wiki/Project-Overview>
- In-repo docs are kept minimal and operational (quickstart, security, contributing).
- Publish wiki pages from repo docs: `scripts/publish_wiki.sh` (details in `docs/WIKI_WORKFLOW.md`).

## Install

Pick the extra that matches your coding agent:

```bash
pip install prpt[claude]      # for use with claude-code (Claude Haiku SLM)
pip install prpt[codex]       # for use with codex (GPT-5.4-nano SLM)
pip install prpt[all]         # both
```

`[anthropic]` / `[openai]` are kept as aliases for backward compatibility.
