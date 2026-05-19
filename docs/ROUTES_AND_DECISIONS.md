# Routes and Decisions

PromptPilot's harness chooses what should happen before a frontier coding agent runs.

## Route types

| Route | Use when | Result |
|---|---|---|
| Clarify | The request is ambiguous or underspecified | Ask one focused question and stop |
| Answer | The request does not need code execution | Reply directly |
| Passthrough | Transformation risk is high | Send the original context unchanged |
| Rewrite | The request is clear and safe to sharpen | Preserve intent while improving clarity |
| Compress | Tool output is noisy but low-risk | Summarize while preserving debugging facts |
| Invoke agent | Coding work is needed | Send preserved context to Codex/Claude-style agent |

## Decision criteria

The harness should consider:

- Is the user asking for code changes, explanation, setup, or troubleshooting?
- Are there explicit constraints, non-goals, files, commands, tests, or stack traces?
- Would rewriting remove important context?
- Would a clarification question prevent wasted work?
- Can a direct answer satisfy the request without invoking the coding agent?

## Examples

These examples are schematic and show the decision shape, not a literal CLI or API output format.

Clarify:

```text
Input: "Fix auth."
Route: clarify
Question: Which auth failure should I focus on: login, token refresh, or permission checks?
```

Passthrough:

```text
Input: "Fix this production migration. Do not change the public schema contract."
Route: passthrough
Reason: High-risk database/API constraints should reach the coding agent unchanged.
```

Compress:

```text
Input: 800 repeated pytest stack frames
Route: compress
Preserve: failing test name, exception, top relevant stack frame, command, changed files.
```

## Related pages

- [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness)
- [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation)
- [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression)
