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

---

**See also:** [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) · [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
