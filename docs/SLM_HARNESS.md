# SLM Harness

PromptPilot uses a small language model as the harness around a frontier coding agent.

## Why use an SLM?

Many coding-agent sessions contain control decisions that do not require a frontier model:

- Is this prompt ambiguous?
- Is this mostly repeated log output?
- What are the important file paths?
- What user constraints must be preserved?
- Should the prompt be passed through unchanged?
- Is this a simple answer that does not require agent execution?

The SLM controls the workflow; it does not replace the coding agent.

## What the SLM is trusted to do

- Extract constraints
- Classify intent
- Detect ambiguity
- Compress repetitive output
- Recommend route
- Preserve structured facts
- Flag high-risk transformations for passthrough

## What the SLM is not trusted to do

- Implement complex code changes
- Debug deep logic bugs
- Infer hidden product requirements
- Drop constraints to save tokens
- Make irreversible changes
- Override explicit developer instructions

## Fallback principle

When uncertain, passthrough.

The SLM is useful only when it reduces noise without changing intent. A slightly more expensive run is better than a cheap but wrong run.

## Harness outputs

A successful harness result should make the downstream agent's job clearer
while preserving meaning. The SLM emits an `ExecutionSpec` whose `route` is
one of four values:

- `clarify` — a clarification question when the request is ambiguous.
- `answer` — a direct SLM reply when no coding-agent execution is needed (only surfaced interactively when the user opts in via `--let-slm-answer`).
- `passthrough` — pass the raw prompt unchanged when rewrite risk is high.
- `act` — invoke the coding agent with a constraint-preserving `downstream_prompt`.

A separate `PostToolUse` hook (not part of the SLM's route choice) compresses
noisy bash tool output — pytest traces, grep floods, installer logs, large
diffs — using regex/heuristic rules that keep failures, paths, stack frames,
commands, and API boundaries. See [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression).

## Output format: prose envelope vs JSON spec

PromptPilot has two SLM output formats. The downstream coding agent **always
receives plain text** (the rewritten prompt) — the format below is what the
SLM emits to PromptPilot, which then extracts the text and forwards it.

### Default: prose envelope (v1)

`--normalizer slm-anthropic`, `--normalizer slm-openai`, and the auto-detected
default `slm` all use this format. The SLM emits a structured-header preamble,
a `---` separator, and the rewritten prompt:

```text
INTENT: act
SCOPE: localized
---
Fix the failing auth test without changing public API behavior or migration files.
```

PromptPilot parses the header lines, then forwards everything after the
separator to the coding agent. Simple, regex-based parser; no SDK
dependencies.

### Experimental: JSON ExecutionSpec (v2)

`--normalizer slm-openai-v2` makes the SLM emit a single JSON object carrying
the full execution spec ([prpt/core/spec.py#L27](https://github.com/steyangdot/PromptPilot/blob/main/prpt/core/spec.py#L27)):

```json
{
  "route":            "act",
  "intent":           "act",
  "scope":            "localized",
  "needs_history":    false,
  "context_policy":   "targeted",
  "target_files":     ["src/auth/login.py", "tests/test_auth.py"],
  "risk":             "low",
  "downstream_prompt": "Fix the failing auth test without changing public API behavior or migration files.",
  "memory_record":    "User wants the failing auth test fixed; preserve public API and migrations."
}
```

PromptPilot extracts `downstream_prompt` and forwards it to the coding agent
exactly as in v1; the other fields drive internal routing
(`route` &rarr; clarify / answer / passthrough / act), context loading
(`context_policy`, `target_files`), and session memory (`memory_record`
becomes the next turn's assistant record).

### Why two formats?

The prose envelope carries only two structured fields (INTENT + SCOPE). The
JSON spec carries nine. v2 was added when the harness grew features that
needed more than INTENT/SCOPE to drive — the routing decision (`route`),
constraint-preservation hints (`target_files`, `context_policy`), risk
classification (`risk`), and a session-memory record (`memory_record`).
JSON also parses more reliably than free-form prose at the cost of a
slightly heavier system prompt.

**Fail-open by design.** If the v2 normalizer's JSON parse fails, the code
falls back to the v1 prose parser
([prpt/normalizers/slm_openai_v2.py#L144-L165](https://github.com/steyangdot/PromptPilot/blob/main/prpt/normalizers/slm_openai_v2.py#L144-L165)).
If both fail, heuristic defaults (`intent=act`, `scope=localized`) keep the
pipeline running. The downstream coding agent never sees broken JSON.

**v1 is the default** because the prose envelope is simpler, ships across all
SLM backends (Anthropic / OpenAI / subscription), and has more production
mileage. v2 is opt-in until the JSON-spec path is validated on more workloads.
The mapping from `route` values to harness behavior is documented in
[Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions).

## Example

This is a schematic example showing both formats for the same input.

Raw prompt:

```text
Fix the failing auth test. Keep the public API stable and do not touch migrations.
```

Default (v1 prose envelope):

```text
INTENT: act
SCOPE: localized
---
Fix the failing auth test without changing public API behavior or migration files.
```

v2 (JSON spec):

```json
{"route":"act","intent":"act","scope":"localized","needs_history":false,
 "context_policy":"targeted","target_files":["src/auth/login.py"],"risk":"low",
 "downstream_prompt":"Fix the failing auth test without changing public API behavior or migration files.",
 "memory_record":"User wants failing auth test fixed; preserve public API + migrations."}
```

Either way, the coding agent receives only the `downstream_prompt` text. The
SLM clarifies the work envelope; the coding agent performs the implementation.

---

**See also:** [Routes and Decisions](https://github.com/steyangdot/PromptPilot/wiki/Routes-and-Decisions) · [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation) · [Safety Model](https://github.com/steyangdot/PromptPilot/wiki/Safety-Model)
