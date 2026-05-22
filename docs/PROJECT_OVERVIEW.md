# Project Overview

**PromptPilot pays API cents for the cheap harness calls and routes the expensive coding-agent calls to a subscription you already pay for.** Same prompt, fewer wasted agent turns, and explicit constraints preserved on the way through. In one measured chain run the SLM layer cost ~$0.0085 of real API spend while the agent work would cost ~$38 at per-token API rates — but that's a *marginal* gap, not free work: the agent side consumes subscription quota (fixed monthly fee + finite ceiling). Single workload; the pattern, not the multiplier, is the point.

PromptPilot is an SLM-powered control plane for AI coding agents.

It sits before Codex/Claude-style tools and uses a small model to make bounded workflow decisions: clarify ambiguous prompts, answer simple non-coding requests, pass through high-risk context unchanged, compress noisy tool output, carry bounded cross-turn [session memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory) so follow-ups resolve references cheaply, and preserve constraints before invoking the frontier coding agent.

The goal is not to replace the coding model. The goal is to make expensive agent runs clearer, safer, and less wasteful.

This page is the conceptual overview. The repository [README](https://github.com/steyangdot/PromptPilot/blob/main/README.md) stays intentionally shorter and focuses on install, package metadata, and the first link into the docs.

## What problem it solves

AI coding sessions often waste frontier-model context on work that does not require frontier reasoning:

- Ambiguous prompts that should be clarified before execution.
- Simple requests that can be answered without running the coding agent.
- Repeated logs, stack traces, grep floods, installer output, and large diffs.
- Prompt rewrites that accidentally drop file paths, tests, flags, or user constraints.
- Re-explaining prior turns across separate invocations — or paying the native tool's full transcript replay every turn (which grows unbounded; PromptPilot's bounded session stays flat — see [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory)).

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
