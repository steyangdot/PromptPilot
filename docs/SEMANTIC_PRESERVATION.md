# Semantic Preservation

PromptPilot treats semantic preservation as the safety condition for every SLM harness decision.

Shorter output is not automatically better. If a rewrite or compression drops an important constraint, path, failing test, stack trace, command, or API boundary, the token savings are fake: the user may pay them back through bugs, rework, and debugging time.

## Preserve by default

The harness should preserve:

- Explicit user constraints and non-goals
- File paths, symbols, package names, commands, and flags
- Failing tests, exceptions, stack frames, and error codes
- API contracts, public behavior, migration constraints, and security requirements
- User-provided priorities such as performance, compatibility, or minimal diff size

## Compress carefully

PromptPilot should compress low-risk noise:

- Repeated stack frames
- Duplicate grep matches
- Long installer progress logs
- Repeated linter formatting
- Irrelevant unchanged context in large diffs

Compression is only valid when critical facts survive.

## Pass through on risk

The correct route is passthrough when the SLM cannot confidently preserve intent. Passthrough is not a failure; it is the safe fallback that lets the frontier coding agent reason over the original context.

## Example

This is a schematic example, not a literal wire format.

Risky input:

```text
The flaky test only fails after the retry window expires. Do not change the timeout constant.
```

Bad compression:

```text
Fix flaky retry test.
```

This drops the timing condition and the explicit non-goal. A safe harness output must preserve both:

```text
Fix the flaky retry test. Preserve the existing timeout constant and investigate the failure after the retry window expires.
```

---

**See also:** [SLM Harness](https://github.com/steyangdot/PromptPilot/wiki/SLM-Harness) · [Safety Model](https://github.com/steyangdot/PromptPilot/wiki/Safety-Model) · [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks)
