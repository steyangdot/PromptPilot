# Routes and Decisions

PromptPilot's harness chooses what should happen before a frontier coding agent
runs. Every developer prompt the harness sees gets one of four route values in
the SLM's execution spec.

## Route types

| Route (spec value) | Use when | Result |
|---|---|---|
| `clarify` | The request is ambiguous or underspecified | Ask one focused question and stop |
| `answer` | The request does not need code execution | Offer a direct SLM reply (opt-in via `--let-slm-answer`); otherwise forward to the coding agent |
| `passthrough` | Transformation risk is high | Send the raw prompt to the coding agent unchanged |
| `act` | Coding work is needed | Forward to the coding agent (Codex / Claude-style). The SLM produces a `downstream_prompt` that preserves constraints and sharpens the original |

These map 1:1 to the `route` field on the SLM's `ExecutionSpec` JSON (emitted by
the v2 normalizers `slm-anthropic-v2` / `slm-openai-v2` for API keys and
`slm-subscription-v2` for Max OAuth / ChatGPT — the default `slm` backend
auto-selects the v2 normalizer matching whichever auth you have). The legacy v1
normalizers (`slm-anthropic` / `slm-openai` / `slm-subscription`) carry only the
`INTENT:` header in a prose envelope and so always resolve to `act`/`answer` —
`clarify` and `passthrough` require a v2 backend. The downstream coding agent always receives plain text
either way — see [SLM Harness &rarr; Output format](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness#output-format-prose-envelope-vs-json-spec)
for the side-by-side example.

The README demo poster shows this `clarify` &rarr; `act` flow end to end on a real
`slm-anthropic-v2` run.

### Rewrite is a behavior, not a route

When the route is `act`, the SLM produces a rewritten `downstream_prompt`
alongside the spec. "Rewrite" is therefore not a separate routing decision —
it's the default work the SLM does inside `act`. Older drafts of these docs
listed Rewrite as its own route; that conflated the route (where the prompt
goes) with the transformation (what the prompt looks like when it gets there).

### Tool-output compression is a different subsystem

Compression of noisy tool output (`pytest`, `grep`, `git diff`, installer
logs, ...) is **not** a route the harness picks for the user's prompt. It runs
as a separate `PostToolUse` hook against the coding agent's bash tool output,
*after* the tool runs. See [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression)
for the wiring and what the regex-based compression preserves.

## Decision criteria

The SLM should consider:

- Is the user asking for code changes, explanation, setup, or troubleshooting?
- Are there explicit constraints, non-goals, files, commands, tests, or stack traces?
- Would rewriting remove important context? (If yes — route to `passthrough`.)
- Would a clarification question prevent wasted work? (If yes — `clarify`.)
- Can a direct answer satisfy the request without invoking the coding agent? (If yes — `answer`.)
- Otherwise — `act`, with a constraint-preserving rewrite.

## Examples

These examples are schematic. The real wire format is the JSON `ExecutionSpec`;
the human-readable rendering below shows the decision shape.

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

Act (with rewrite):

```text
Input: "Fix the failing auth test. Keep the public API stable and do not touch migrations."
Route: act
downstream_prompt: Fix the failing auth test without changing public API behavior or migration files.
Preserve: failing auth test, public API stability, no migration edits.
```

---

**See also:** [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation) · [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression)
