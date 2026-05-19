# Tool Output Compression

PromptPilot can compress noisy tool output before it reaches the coding agent.

The target is low-value repetition: long pytest traces, grep floods, installer progress logs, verbose diffs, and linter output. Compression is only valid when important debugging facts survive.

## What to preserve

Compressed output should keep:

- The command that ran.
- Exit status or error code.
- Failing test names.
- Exception names and messages.
- Relevant file paths and symbols.
- The most useful stack frames.
- User constraints and non-goals.

## What can usually shrink

- Repeated stack frames.
- Duplicate grep matches.
- Long progress bars.
- Repeated dependency install logs.
- Large unchanged diff context.

## Claude Code setup

Add a `PostToolUse` hook for Bash output in Claude Code settings. The hook should call PromptPilot's compression script and preserve a reasonable timeout.

## Codex setup

Codex picks up the repo's `.codex/hooks.json` when run inside a PromptPilot checkout.

## Review compression results

Use:

```bash
prpt stats --last 10
```

Compression telemetry is recorded so you can audit what changed and whether the savings are worth it.

## Related pages

- [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation)
- [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
- [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks)
