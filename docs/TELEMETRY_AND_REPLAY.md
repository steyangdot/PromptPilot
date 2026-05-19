# Telemetry and Replay

PromptPilot records enough information to make harness behavior inspectable.

Telemetry should help answer:

- Which route did the harness choose?
- Why was a prompt clarified, rewritten, compressed, or passed through?
- How many tokens were saved?
- Were critical facts preserved?
- What changed across a session restart or handoff?

## Common commands

Review recent run summaries:

```bash
prpt stats --last 10
```

Create a handoff snapshot:

```bash
prpt checkpoint
```

Restart with a compact summary:

```bash
prpt restart
```

Bootstrap from a curated handoff:

```bash
prpt bootstrap
```

## Handoff contents

A valid handoff should keep:

- Goal
- Decisions made
- Files touched
- Open items
- Constraints

These sections make restarts auditable and reduce the chance that important context disappears.

## Related pages

- [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart)
- [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression)
- [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks)
