# Tool-output compression example

This example category is for noisy command output that can be compressed without losing debugging signal.

Good candidates include:

- Repeated pytest tracebacks.
- grep or ripgrep floods.
- verbose git diffs.
- linter output with repeated formatting.
- installer logs with a small number of important failures.

The compressed output should preserve the command, failing package or test, error code, file paths, symbols, and stack frames needed by the coding agent.
