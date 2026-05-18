# Roadmap

PromptPilot's roadmap emphasizes two principles:

1. The SLM remains core to the control layer.
2. The SLM remains bounded and should pass through when uncertain.

## Near term

- Improve route confidence reporting for clarify, answer, passthrough, compress, and invoke-agent decisions.
- Expand semantic-preservation checks for file paths, tests, stack traces, commands, and explicit constraints.
- Add more example cases for passthrough and tool-output compression.
- Make telemetry easier to inspect during replay and audit.

## Medium term

- Add benchmark fixtures that score both token reduction and preservation.
- Compare SLM harness behavior across providers and model sizes.
- Surface high-risk transformations before invoking the coding agent.
- Improve docs for integrating PromptPilot with Codex/Claude-style workflows.

## Non-goals

- Replacing frontier coding models.
- Hiding critical context to maximize token savings.
- Letting the SLM make irreversible code changes.
