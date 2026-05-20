# Roadmap

PromptPilot's roadmap holds two principles:

1. The SLM stays the harness brain — it does not become the coder.
2. When the SLM is uncertain, the correct action is passthrough.

## Shipped

The pieces below are in `main` and covered by the test suite.

- **v2 control plane** — JSON `ExecutionSpec` with `route ∈ {clarify, answer, passthrough, act}` plus `intent`, `scope`, `target_files`, `risk`, `memory_record`. v1 prose envelope kept as fallback parser. ([prpt/core/spec.py](https://github.com/steyangdot/PromptPilot/blob/main/prpt/core/spec.py), [prpt/normalizers/slm_openai_v2.py](https://github.com/steyangdot/PromptPilot/blob/main/prpt/normalizers/slm_openai_v2.py))
- **Four interchangeable judges** — `MaxHaikuJudge`, `CodexCliJudge`, `AnthropicApiJudge`, `OpenAiJudge`, auto-detected in priority order. ([prpt/judges/judge.py](https://github.com/steyangdot/PromptPilot/blob/main/prpt/judges/judge.py))
- **Hybrid auth pattern** — API-key SLM + subscription LLM, ~4,500× subsidy ratio measured on chain5. ([Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode), [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks))
- **Handoff / restart workflow** — `prpt checkpoint`, `prpt bootstrap`, `prpt restart` for collapsing heavy sessions to `handoff.md` and resuming fresh. ([prpt/handoff.py](https://github.com/steyangdot/PromptPilot/blob/main/prpt/handoff.py))
- **Session memory with referential gate** — recent turns prepended; `--gate-session` classifier skips history when prompt is self-contained. Default-on after N=5 chain1 retest showed +60% success / −28.7% cps for WITH-session.
- **Onboarding subcommands** — `prpt setup` (one-time, with smoke test), `prpt doctor` (re-check, no install), `prpt install-hook` for both Claude Code and Codex.
- **Tool-output compression hook** — regex-based `PostToolUse` compressor for pytest / grep / git diff / installer logs. ([.codex/hooks/compress_tool_output.py](https://github.com/steyangdot/PromptPilot/blob/main/.codex/hooks/compress_tool_output.py))
- **Compliance posture documentation** — side-by-side breakdown vs the OpenClaw / OpenCode pattern enforced April 2026. ([Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison))
- **Wiki publishing automation** — `scripts/publish_wiki.sh` keeps the wiki mirrored from `docs/`. ([Wiki Publishing](https://github.com/steyangdot/PromptPilot/wiki/Wiki-Publishing))

## In progress

- **Honest measured benchmarks** — chain1, chain4, chain5 numbers are landed; broader workload coverage (Django / Rails / JS repos) not yet measured.
- **Route-accuracy fixture set** — labelled prompts to score `clarify` / `answer` / `passthrough` / `act` decisions, not just per-call cost.
- **Preservation recall metrics** — programmatic checks for file paths, test names, commands, flags, symbols, stack frames, and explicit constraints.

## Planned

- Cross-provider / model-size A/B for the SLM harness (Haiku vs gpt-5.4-nano vs gpt-5.4-mini).
- Surface high-risk transformations to the user before invoking the coding agent.
- More worked passthrough and compression examples.
- Easier telemetry inspection during replay (current `prpt stats` is a flat JSONL summary).

## Non-goals

- Replacing frontier coding models with the SLM.
- Maximizing token reduction when context may be lost.
- Letting the SLM make irreversible code changes.
- Token handling for subscription auth (we invoke the official binary; the credential never enters our process).

---

**See also:** [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks) · [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay) · [Wiki Publishing](https://github.com/steyangdot/PromptPilot/wiki/Wiki-Publishing)
