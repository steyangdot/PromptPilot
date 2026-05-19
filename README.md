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

For the full guide see **[QUICKSTART.md](QUICKSTART.md)** and `prpt --help`
(or `prpt --advanced-help` for internal/researcher flags).
