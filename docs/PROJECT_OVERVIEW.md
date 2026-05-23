# Project Overview

PromptPilot helps Codex and Claude-style coding agents spend less context on avoidable work.

Long coding sessions often burn frontier-model tokens on ambiguous prompts, repeated session history, noisy tool output, and constraints that should have been made explicit before coding starts. PromptPilot adds a small language model (SLM) control layer in front of those agents to make the work clearer before the expensive model starts coding.

The goal is not to replace the frontier coding model with a small language model. The SLM manages bounded workflow decisions; the frontier model remains responsible for code understanding, implementation, debugging, and test repair.

This page is the conceptual overview for readers evaluating the idea. If you want to run PromptPilot, start with [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart). If you want the full rendered documentation, use the [PromptPilot GitHub Wiki](https://github.com/steyangdot/PromptPilot/wiki).

## What problem it solves

AI coding sessions often waste frontier-model context on work that does not require frontier reasoning:

- Ambiguous prompts that should be clarified before execution.
- Simple requests that can be answered without running the coding agent.
- Repeated logs, stack traces, grep floods, installer output, and large diffs.
- Prompt rewrites that accidentally drop file paths, tests, flags, or user constraints.
- Re-explaining prior turns across separate invocations, or paying the native tool's full transcript replay every turn.

PromptPilot treats these as control-layer decisions. It tries to send the frontier model a clearer, safer task, while preserving the user's intent and constraints.

## Core idea

PromptPilot sits before the coding agent and chooses the safest useful handling for each request:

- `clarify` when a prompt is too ambiguous to execute safely.
- `answer` when a simple explanation can be handled without a coding-agent call.
- `passthrough` when rewriting would risk changing the user's meaning.
- `act` when the request should be rewritten and sent to the coding agent.

That routing model is intentionally limited. PromptPilot is not trying to make deep implementation decisions. It is trying to decide how to package the work before the frontier model sees it.

For the detailed route model, see [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) and [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness).

## Design principles

PromptPilot optimizes for **semantic-preserving context control**, not blind token reduction. A rewritten prompt may be longer than the original when that preserves important constraints.

The project leans on a few principles:

- Preserve meaning before reducing tokens.
- Pass through high-risk prompts rather than forcing a rewrite.
- Keep the frontier model responsible for coding work.
- Keep cross-turn memory bounded instead of replaying an ever-growing transcript.
- Compress noisy tool output only when the compression preserves debugging facts.
- Treat measured token savings as workload-dependent, not universal guarantees.

The deeper details live in the focused docs: [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation), [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory), [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression), and [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks).

## Mental model

```text
Developer request
  -> PromptPilot control layer
  -> route decision: clarify | answer | passthrough | act
  -> Codex/Claude-style coding agent, when code work is needed
  -> optional hooks compress noisy tool output
  -> telemetry supports review and replay
```

The important split is responsibility:

- PromptPilot manages workflow context, prompt shape, session summaries, and safety-oriented routing.
- The coding agent performs repository understanding, editing, debugging, and test repair.

For implementation structure, see [Architecture](https://github.com/steyangdot/PromptPilot/wiki/Architecture). For operational traces and replay, see [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay).

## Measured example

In one measured 15-turn chain, about 24k input tokens of SLM work directed about 12.66M input tokens of agent work. The control layer was about 0.2% of the input-token footprint, and the bounded session ran the same multi-turn work on about 7.6x fewer input tokens than the tool's native session.

This is a measured example, not a guarantee. The useful claim is not "PromptPilot always makes prompts shorter." The claim is that a small control layer can preserve intent, bound session context, and reduce avoidable frontier-model work on suitable workloads.

For the full evidence and caveats, see [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks) and [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode).

## When to use PromptPilot

Use PromptPilot when:

- You already work with Codex or Claude-style coding agents.
- You want clearer prompts and fewer unnecessary agent calls.
- You want bounded session memory for multi-turn workflows.
- You want hook-based compression that preserves debugging facts.
- You care about auditability and repeatable handoff/restart workflows.

## When not to use it

PromptPilot is not a good fit when:

- You want a standalone coding agent.
- You expect the small language model to make deep implementation decisions.
- You want maximum token reduction even when context may be lost.
- You do not want a passthrough fallback for high-risk requests.

## Where this fits in the docs

This page belongs in the wiki's "Evaluating the idea" path. It gives the mental model without replacing the focused pages. The wiki home remains the authoritative navigation map.

- Trying PromptPilot: [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart)
- Evaluating the design: [Architecture](https://github.com/steyangdot/PromptPilot/wiki/Architecture) and [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison)
- Understanding harness behavior: [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness), [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions), and [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation)
- Operating it: [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers), [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression), and [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
