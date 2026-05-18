# Semantic preservation example

This example category is for prompts where the harness must preserve explicit meaning before any rewrite or compression.

A good fixture should include:

- File paths or symbols that must survive.
- A failing test, stack trace, or command when relevant.
- User constraints such as "do not change the public API" or "minimal diff only".
- An expected harness output that keeps those facts intact.

Success is not shorter text alone. Success means the frontier coding agent receives the critical facts it needs.
