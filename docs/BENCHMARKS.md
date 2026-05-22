# Benchmarks

PromptPilot measures the SLM harness on two dimensions:

1. **Cost** — does routing through a small model pay for itself?
2. **Preservation** — do critical facts survive the rewrite?

A harness output is not successful just because it is shorter. Token reduction without preservation makes the expensive coding agent cheaper but less informed — usually a net loss.

## Measured results

These numbers come from the in-repo chain harness ([research/chain_test_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/research/chain_test_v2.py)) against a real target repo (`httpx`). Each row is a specific experiment with a stable identifier so re-runs are reproducible.

| Experiment | Setup | What was measured | Result |
|---|---|---|---|
| chain5 codex hybrid | API-key Haiku SLM + ChatGPT-subscription codex LLM | Token footprint by layer (15-turn chain) | **SLM control layer ~24k input tokens directs ~12.66M agent input tokens — ~0.2% overhead shapes 99.8% of the work.** Hybrid routes the 0.2% to cheap metered API, the rest to a flat-fee subscription. (≈ $0.0085 API + ~$38-at-list-rates of agent tokens, but tokens are the measured fact — see caveat.) |
| Codex CLI vs OpenAI SDK | Same prompt through `CodexCliJudge` and `OpenAiJudge` | Per-call cost | **SDK path ~100× cheaper per call**; codex CLI burns ~19k input tokens of agent-loop overhead per invocation |
| `--gate-session` (chain4 N=10, v2 record) | With vs without session-history gating under the v2 short memory_record | Input tokens, success delta, cost-per-success | **−4.6% input tokens, −0.25 sum success, +5.0% cps** — net loss under v2; gate matters more on long-session workloads |
| Session memory value, **claude-code** (chain1 N=5) | WITH session vs NO session (both SLM-rewritten — clean isolation) | Success rate, cost-per-success | **+60% success, −28.7% cps** in favor of WITH-session |
| Session memory value, **codex** (chain1 N=5) | WITH session vs NO session | Success, input tokens | **success tied (1.70 vs 1.90, within noise), −20% input tokens** — on codex, session is a *cost* optimization, not a quality lift |
| Session vs native `exec resume`, **codex** (chain1 N=5) | full PromptPilot vs raw-prompt + native codex session | Cost-per-success, input growth | **~8.5× cheaper per success** at equal quality; native input grows **465k→2.36M** across 5 turns (unbounded transcript) while PromptPilot stays flat ~44k/turn |

See [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory) for the full breakdown and the bounded-vs-unbounded mechanism.

Caveats:
- Single workload (`httpx`). Your repo will land somewhere different.
- **Session value is tool-dependent:** the +60% success lift is **claude-code-specific**; codex shows session as a cost optimization (tied success). Don't quote +60% as a universal PromptPilot number.
- The "~8.5× cheaper" (and the analogous claude-code "~3× cheaper than `--resume`") compares *full PromptPilot* (SLM rewrite + bounded session) against a *raw-prompt + native-session* baseline — so the ratio bundles the rewrite benefit with the session-mechanism benefit. It's a product comparison, not an isolated session-only number. The transcript-growth curve is the clean session-mechanism evidence.
- N=5 success deltas under ~0.2/turn are within the noise floor; cost gaps are the robust signal.
- **Lead with tokens, not dollars.** Tokens are measured directly and are provider-neutral; dollar figures require assuming both API rates and subscription terms (the assumption that made an earlier "$38 vs $0.0085, 4,500× subsidy" framing misleading — it treated finite, flat-fee subscription quota as free). The honest, durable numbers are the token footprints: ~24k SLM tokens directing ~12.66M agent tokens (hybrid split), and ~7.6× fewer input tokens than native session (efficiency). What those tokens cost is downstream: per-token on metered API, or a slice of finite subscription quota (which sustained runs exhaust — we hit the ChatGPT usage limit mid-experiment, May 2026; use the API path for high-volume automation). See [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode).
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
