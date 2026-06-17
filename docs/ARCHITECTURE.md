# Architecture

PromptPilot is built around one idea:

> A small language model can serve as the harness brain around a larger coding agent.

The SLM is not the coder. It is the control layer.

It decides:

- What is the user asking?
- Is the prompt clear enough?
- Is this safe to rewrite, or should we pass it through unchanged?
- Should the request be answered directly?
- What facts must be preserved when the coding agent is invoked?

A separate post-tool subsystem decides:

- Should noisy bash tool output be compressed before it re-enters the agent's context?

The large coding agent remains responsible for:

- Code understanding
- Implementation
- Debugging
- Test repair
- Architectural reasoning

```text
Developer prompt
        ↓
PromptPilot
        ↓
SLM harness
  - intent
  - ambiguity
  - risk
  - preservation
        ↓
Route (one of four)
  - clarify
  - answer
  - passthrough
  - act    (with constraint-preserving rewrite -> downstream_prompt)
        ↓
Codex / Claude-style coding agent
        ↓
Tool output (bash) ── PostToolUse hook ──> regex compressor ──> back to agent
        ↓
Telemetry / replay / audit
```

## Runtime layers

PromptPilot separates workflow control from coding execution:

1. **Input capture** receives the developer prompt and session memory.
2. **SLM harness** classifies intent, extracts constraints, evaluates ambiguity, and estimates transformation risk.
3. **Route selection** chooses one of four routes: `clarify`, `answer`, `passthrough`, or `act` (which carries a constraint-preserving rewrite in `downstream_prompt`).
4. **Frontier coding agent** receives either the raw prompt (`passthrough`) or the SLM's `downstream_prompt` (`act`).
5. **Tool-output compression** (separate subsystem) runs as a `PostToolUse` hook on the agent's bash output, shrinking pytest/grep/git/installer noise before it re-enters the agent's context. Regex/heuristic-based, not SLM-driven.
6. **Telemetry and replay** record routing, token impact, compression events, and session handoff data.

This keeps the SLM central but bounded: it controls the workflow around the coding agent without replacing the model that reasons about and edits code.

## Session memory (bounded)

Multi-turn work needs continuity, but a tool's native session re-feeds the whole transcript every turn — cheap on Claude Code (its `--resume` caches history), costly on Codex (its transcript grows uncached). PromptPilot keeps a **bounded session memory** instead: one short record per turn — the SLM's one-line `memory_record` (intent + constraints) plus the files the agent changed — not the full replay.

The choice is **tool-aware**: bound the session on Codex; defer to native `--resume` on Claude Code. Either way the agent keeps enough thread to handle referential follow-ups ("add a test for that") without paying to re-read everything. Distilling each turn into the `memory_record` is itself SLM work — the frontier agent never summarizes itself.

## The v2 control spec

The SLM returns more than a rewritten string. The **v2 normalizer** emits one structured decision — a JSON `ExecutionSpec` carrying the **route**, **intent**, **scope**, **target files**, a **risk** estimate, and the one-line **`memory_record`**. That single object drives the whole control layer: it selects the route, grounds the rewrite (target files + constraints), and seeds the bounded session (the `memory_record`).

One guard matters for automation: in **autonomous mode** (`PROMPTPILOT_AUTONOMOUS=1`, for agent/CI use), a `clarify` route **degrades to `act`** — with no human to answer, the agent acts on the original request instead of asking itself a question. Interactive use is unchanged (a human still sees the clarifying question).

---

**See also:** [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) · [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory) · [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
