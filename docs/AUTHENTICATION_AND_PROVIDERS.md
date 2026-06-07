# Authentication and Providers

PromptPilot can run with subscription-authenticated CLIs, API keys, or a hybrid of both. You only need one working path to start.

## Provider paths

| Path | Setup | Best for |
|---|---|---|
| Claude Max/Pro subscription | `claude auth login --claudeai` | Claude users who want minimal setup |
| ChatGPT/Codex subscription | `codex login` | Codex users who want minimal setup |
| Anthropic API key | `ANTHROPIC_API_KEY=...` in `.env` | Faster SDK calls and predictable per-call billing |
| OpenAI API key | `OPENAI_API_KEY=...` in `.env` | Fast small-model calls via SDK |
| Hybrid | API key for SLM + subscription CLI for downstream agent | Daily users who already have a subscription — see [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode) for setup and tradeoffs |

## How to choose

Start with the path that matches the coding agent you already use.

- Claude Code users can start with `claude auth login --claudeai`.
- Codex users can start with `codex login`.
- Users who need faster or more predictable SLM calls should use an API key.
- Users with both a subscription and an API key can route cheap harness work through the API and expensive coding work through the subscription CLI.

## Environment knobs

Common settings include:

- `ANTHROPIC_API_KEY` for Anthropic SDK calls.
- `OPENAI_API_KEY` for OpenAI SDK calls.
- `PROMPTPILOT_JUDGE` to choose the small-model judge path (`max | codex | anthropic | openai`).
- `PROMPTPILOT_LET_SLM_ANSWER=1` to opt into the interactive SLM-direct-answer dialog on explain prompts.

> **Note:** `CLAUDE_MODEL` and `USE_MAX_AUTH` appear in some chain-harness scripts under `research/` but are **not consumed by the `prpt` CLI itself.** Setting them on a normal `prpt` invocation has no effect.

## Normalizers and defaults

The default `slm` normalizer **auto-selects a v2 (JSON `ExecutionSpec`) normalizer to match your auth**:

| Auth present | Normalizer chosen |
|---|---|
| `ANTHROPIC_API_KEY` | `slm-anthropic-v2` |
| `OPENAI_API_KEY` | `slm-openai-v2` |
| Max OAuth / ChatGPT subscription | `slm-subscription-v2` |

v2 normalizers emit the routing decision (`route` = answer / act / **clarify** / passthrough) alongside the rewrite. The legacy v1 prose normalizers (`slm-anthropic` / `slm-openai` / `slm-subscription`) only emit `act`/`answer`; pick them explicitly with `--normalizer` for pre-v2 behavior. `PROMPTPILOT_JUDGE` is a **separate** setting (the checkpoint/restart judge backend), not the normalizer.

### Inspecting the routing decision

- `prpt preview` — interactive playground: type a prompt, see the routing spec (JSON) + rewrite, nothing forwarded to an agent.
- `prpt --show-spec "..."` — print the parsed `ExecutionSpec` for one run.
- `PROMPTPILOT_V2_RAW_LOG=1` — log each raw model JSON response to `~/.promptpilot/v2_slm_raw.jsonl`.

## Security notes

- Keep API keys in `.env` or your shell environment, not in committed files.
- Prefer repo-local examples such as `.env.example` for documentation.
- If a shell environment variable shadows `.env`, PromptPilot should report that clearly.

## Compliance posture for subscription routing

When you run against the Max or ChatGPT subscription, PromptPilot invokes the
official `claude` / `codex` binary as a subprocess. The OAuth token stays
inside that binary; PromptPilot never reads or transmits it. This is
structurally different from the third-party Claude harnesses (OpenClaw,
OpenCode, etc.) that Anthropic enforced against in April 2026 — those tools
extracted the OAuth credential and impersonated Claude Code by calling the
API directly. See the side-by-side breakdown in [Comparison &rarr; Compliance posture](https://github.com/steyangdot/PromptPilot/wiki/Comparison#compliance-posture-vs-openclaw--opencode).

For sustained or high-volume automation, the conservative path is the
`ANTHROPIC_API_KEY` SDK normalizer, which sidesteps the interpretive
"ordinary use" framing in Anthropic's Feb 2026 statement.

---

**See also:** [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart) · [Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode) · [Troubleshooting](https://github.com/steyangdot/PromptPilot/wiki/Troubleshooting) · [Safety Model](https://github.com/steyangdot/PromptPilot/wiki/Safety-Model)
