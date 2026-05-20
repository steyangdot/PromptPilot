# Project Overview

**PromptPilot pays API cents for the cheap harness calls and burns subscription credit on the expensive coding-agent calls.** Same prompt, fewer wasted agent turns, and explicit constraints preserved on the way through. One measured chain run drove ~$38 of equivalent agent work for ~$0.0085 of real spend — single workload, but the pattern is the point.

PromptPilot is an SLM-powered control plane for AI coding agents.

It sits before Codex/Claude-style tools and uses a small model to make bounded workflow decisions: clarify ambiguous prompts, answer simple non-coding requests, pass through high-risk context unchanged, compress noisy tool output, and preserve constraints before invoking the frontier coding agent.

The goal is not to replace the coding model. The goal is to make expensive agent runs clearer, safer, and less wasteful.

This page is the conceptual overview. The repository [README](https://github.com/steyangdot/PromptPilot/blob/main/README.md) stays intentionally shorter and focuses on install, package metadata, and the first link into the docs.

## What problem it solves

AI coding sessions often waste frontier-model context on work that does not require frontier reasoning:

- Ambiguous prompts that should be clarified before execution.
- Simple requests that can be answered without running the coding agent.
- Repeated logs, stack traces, grep floods, installer output, and large diffs.
- Prompt rewrites that accidentally drop file paths, tests, flags, or user constraints.

PromptPilot treats these as harness decisions. The small model manages the workflow around the coding agent, while the frontier model remains responsible for code understanding, implementation, debugging, and test repair.

## Typical flow

```text
Developer request
  -> PromptPilot harness
  -> route decision (one of: clarify, answer, passthrough, act)
  -> Codex/Claude-style coding agent (for passthrough or act)
  -> bash tool output passes through the PostToolUse compression hook
     (regex-based; not part of the route decision)
  -> telemetry for review and replay
```

## When to use PromptPilot

Use PromptPilot when:

- You already work with Codex or Claude-style coding agents.
- You want clearer prompts and fewer unnecessary agent calls.
- You want compression that preserves debugging facts.
- You care about auditability and repeatable handoff/restart workflows.

## When not to use it

PromptPilot is not a good fit when:

- You want a standalone coding agent.
- You expect the small model to make deep implementation decisions.
- You want maximum token reduction even when context may be lost.
- You do not want a passthrough fallback for high-risk requests.

---

**See also:** [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart) · [Architecture](https://github.com/steyangdot/PromptPilot/wiki/Architecture) · [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison)
