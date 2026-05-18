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

A successful harness result should make the downstream agent's job clearer while preserving meaning. Typical outputs include:

- A clarification question when the request is ambiguous.
- A direct answer when no coding-agent execution is needed.
- A passthrough recommendation when rewrite risk is high.
- A safe rewrite that preserves constraints and file references.
- A compressed tool-output summary that keeps failures, paths, stack frames, commands, and API boundaries.
