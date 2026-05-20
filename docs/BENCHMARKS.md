# Benchmarks

PromptPilot measures the SLM harness on two dimensions:

1. **Cost** — does routing through a small model pay for itself?
2. **Preservation** — do critical facts survive the rewrite?

A harness output is not successful just because it is shorter. Token reduction without preservation makes the expensive coding agent cheaper but less informed — usually a net loss.

## Measured results

These numbers come from the in-repo chain harness ([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py)) against a real target repo (`httpx`). Each row is a specific experiment with a stable identifier so re-runs are reproducible.

| Experiment | Setup | What was measured | Result |
|---|---|---|---|
| chain5 codex hybrid | API-key Haiku SLM + ChatGPT-subscription codex LLM | Real $ vs equivalent-API agent work | **~$0.0085 real spend drove ~$38 of equivalent agent work — ~4,500× subsidy ratio** |
| Codex CLI vs OpenAI SDK | Same prompt through `CodexCliJudge` and `OpenAiJudge` | Per-call cost | **SDK path ~100× cheaper per call**; codex CLI burns ~19k input tokens of agent-loop overhead per invocation |
| `--gate-session` (chain4 N=10, v2 record) | With vs without session-history gating under the v2 short memory_record | Input tokens, success delta, cost-per-success | **−4.6% input tokens, −0.25 sum success, +5.0% cps** — net loss under v2; gate matters more on long-session workloads |
| Session memory value (chain1 N=5) | WITH session memory vs NO session memory | Success rate, cost-per-success | **+60% success, −28.7% cps** in favor of WITH-session — session loading defaults to on |

Caveats:
- Single workload (`httpx`). Your repo will land somewhere different.
- Cost ratios depend on subscription terms; the 4,500× figure is shadow-dollars against per-token API rates.
- "Success" is judged by an SLM rubric; see [research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py) for the scorer.

## Preservation targets

For compression of bash tool output (separate subsystem from the SLM route decision), the harness is judged on whether critical facts survive — not on raw token reduction.

| Case | What must survive |
|---|---|
| pytest trace | failing test name, exception, file path, top stack frame |
| grep flood | relevant files, matched symbols, line numbers |
| git diff | changed files, behavior changes, risky edits |
| install log | failing package, error code, originating command |

If preservation fails, the correct route is passthrough. A passthrough run costs more tokens but cannot drop a failing test name.

## Interpreting results

Efficiency numbers should always be paired with preservation checks. A run that removes 90% of tokens but drops the failing test name is worse than a passthrough — the agent runs cheaper but has to re-discover the failure.

## What to measure next

- Route accuracy: clarify / answer / passthrough / act, scored against a labelled fixture set.
- Preservation recall for file paths, test names, commands, flags, symbols, stack frames, and explicit constraints.
- Compression ratio **only after** preservation checks pass.
- Cost and latency by provider/model path (Haiku SDK vs Max OAuth vs codex CLI vs OpenAI SDK).
- Regression fixtures that catch unsafe rewrites.

---

**See also:** [Semantic Preservation](https://github.com/steyangdot/PromptPilot/wiki/Semantic-Preservation) · [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode) · [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay) · [Roadmap](https://github.com/steyangdot/PromptPilot/wiki/Roadmap)
