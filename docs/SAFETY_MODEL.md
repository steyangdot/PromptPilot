# Safety Model

PromptPilot's safety model is built on bounded trust.

The SLM is trusted to make harness decisions that are reversible, inspectable, and low risk. It is not trusted to act as the implementation brain or to discard high-risk context just to save tokens.

## Safe actions

- Ask a clarification question before acting.
- Answer directly when no coding-agent run is needed.
- Preserve constraints while lightly rewriting a prompt.
- Compress repetitive output when important facts remain.
- Recommend passthrough when transformation risk is high.

## Unsafe actions

- Silently dropping explicit user constraints.
- Removing file paths, failing tests, or stack traces needed for debugging.
- Inventing product requirements or implementation details.
- Treating token reduction as success without preservation checks.
- Making deep coding decisions that belong to the frontier agent.

## Rule of thumb

If preserving meaning is uncertain, do not transform the input. Pass it through unchanged and let the coding agent handle the full context.
