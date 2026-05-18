# Architecture

PromptPilot is built around one idea:

> A small language model can serve as the harness brain around a larger coding agent.

The SLM is not the coder. It is the control layer.

It decides:

- What is the user asking?
- Is the prompt clear enough?
- Is this safe to rewrite?
- Should the request be answered directly?
- Should it be passed through unchanged?
- Should noisy tool output be compressed?
- What facts must be preserved?
- Should the expensive coding agent be invoked?

The large coding agent remains responsible for:

- Code understanding
- Implementation
- Debugging
- Test repair
- Architectural reasoning

```text
Developer prompt / tool output
        ↓
PromptPilot
        ↓
SLM harness
  - intent
  - ambiguity
  - risk
  - preservation
  - routing
        ↓
Route
  - clarify
  - answer
  - passthrough
  - compress
  - invoke agent
        ↓
Codex / Claude-style coding agent
        ↓
Telemetry / replay / audit
```

## Runtime layers

PromptPilot separates workflow control from coding execution:

1. **Input capture** receives the developer prompt, session memory, and optional tool output.
2. **SLM harness** classifies intent, extracts constraints, evaluates ambiguity, and estimates transformation risk.
3. **Route selection** chooses clarify, answer, passthrough, compress, rewrite, or invoke-agent behavior.
4. **Frontier coding agent** receives either the original prompt or a semantic-preserving harness output.
5. **Telemetry and replay** record routing, token impact, compression events, and session handoff data.

This keeps the SLM central but bounded: it controls the workflow around the coding agent without replacing the model that reasons about and edits code.
