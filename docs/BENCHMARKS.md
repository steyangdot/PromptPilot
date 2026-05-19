# Benchmarks

PromptPilot measures the SLM harness on two dimensions:

1. **Efficiency:** how much low-value context it removes.
2. **Preservation:** whether critical facts survive.

A harness output is not successful just because it is shorter.

| Case | Raw tokens | Harness tokens | Reduction | Preservation target |
|---|---:|---:|---:|---|
| pytest trace | 12,400 | 2,100 | 83.1% | test name, exception, file path, stack frame |
| grep flood | 9,800 | 1,400 | 85.7% | relevant files, symbols, matched lines |
| git diff | 18,200 | 4,900 | 73.1% | changed files, behavior, risky edits |
| install log | 7,600 | 900 | 88.2% | failing package, error code, command |

These figures are illustrative targets for the kinds of cases PromptPilot should measure. If preservation fails, the correct route is passthrough.

## Interpreting results

Efficiency metrics should always be paired with preservation checks. A run that removes 90% of tokens but drops the failing test name is worse than a passthrough run, because it makes the expensive coding agent cheaper but less informed.

## What to measure next

- Route accuracy: clarify, answer, passthrough, compress, or invoke agent.
- Preservation recall for file paths, test names, commands, flags, symbols, stack frames, and explicit constraints.
- Compression ratio only after preservation checks pass.
- Cost and latency by provider/model path.
- Regression fixtures that catch unsafe rewrites.

## Related pages

- [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation)
- [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
- [Roadmap](https://github.com/steyangdot/PromptPilot/wiki/Roadmap)
