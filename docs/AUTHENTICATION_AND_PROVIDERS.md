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

The default `slm` normalizer **auto-selects a v2 (JSON `ExecutionSpec`) normalizer to match your auth**, along with a default SLM model per backend:

| Auth present | Normalizer chosen | Default SLM model |
|---|---|---|
| `ANTHROPIC_API_KEY` | `slm-anthropic-v2` | `claude-haiku-4-5` |
| `OPENAI_API_KEY` | `slm-openai-v2` | `gpt-5.4-nano` |
| Max OAuth | `slm-subscription-v2` | `claude-haiku-4-5` |
| ChatGPT/Codex subscription | `slm-subscription-v2` | `gpt-5.4-mini` |

v2 normalizers emit the routing decision (`route` = answer / act / **clarify** / passthrough) alongside the rewrite. The legacy v1 prose normalizers (`slm-anthropic` / `slm-openai` / `slm-subscription`) only emit `act`/`answer`; pick them explicitly with `--normalizer` for pre-v2 behavior. `PROMPTPILOT_JUDGE` is a **separate** setting (the checkpoint/restart judge backend), not the normalizer.

### Inspecting the routing decision

- `prpt preview` — interactive playground: type a prompt, see the routing spec (JSON) + rewrite, nothing forwarded to an agent.
- `prpt --show-spec "..."` — print the parsed `ExecutionSpec` for one run.
- `PROMPTPILOT_V2_RAW_LOG=1` — log each raw model JSON response to `~/.promptpilot/v2_slm_raw.jsonl`.

### Autonomous clarify guard (`PROMPTPILOT_AUTONOMOUS=1`)

When a v2 normalizer chooses `route = clarify`, it emits a human-style
multiple-choice clarifying question as the downstream prompt. That is correct
for interactive use, but an autonomous coding agent has no human to answer it —
so the agent *answers the question* instead of acting, producing a silent
end-state failure.

Set `PROMPTPILOT_AUTONOMOUS=1` for agent/headless runs. When `route = clarify`
and this flag is set, PromptPilot degrades the route to `act`: it returns the
original imperative and resets the stale spec (route/intent → `act`, scope →
localized, memory_record → original). The guard is applied by all three v2
normalizers (`slm-openai-v2`, `slm-anthropic-v2`, `slm-subscription-v2`) and by
`prpt --auto` / `--dry-run`. Interactive behavior is **unchanged** — the degrade
is off by default, so a human still sees the clarify question.

In the v0.3.1 release the clarify policy was also tightened: `clarify` now fires
only for genuine ambiguity about *what* to change, never merely because a file
or location is unstated (a repo-access agent can grep). The mis-fire is
model-specific — on an actionable imperative, `gpt-5.4-nano` over-clarified
20/20, while `gpt-5.4-mini` and `claude-haiku-4-5` did not. With the guard on,
the seeded-bug chain task returned to a 5/5 end-state pass rate (from 2/5
pre-fix).

## SLM model choice, transport, and the hybrid

Two independent settings affect cost: **which SLM model** does the rewrite, and
**which transport** carries the call (API SDK vs subscription CLI subprocess).

**Transport is agent-uncached-invariant.** For the same SLM model, the
downstream agent sees the same input whether the SLM ran via an API key or via a
subscription CLI. Measured: Haiku via the Anthropic API drove 67,501 uncached
agent tokens/run vs 65,608 via Max (~3%, equal). Transport only changes *where*
the SLM cost lands — dollars on an API key vs quota on a flat subscription — and
adds ~20k tokens/call of CLI subprocess overhead on the subscription path.

**SLM model is first-order for the codex agent.** A bulkier SLM writes bulkier
rewrites and memory records, which are re-fed to the agent every turn. On codex,
`gpt-5.4-mini` inflated agent uncached ~1.6× vs `gpt-5.4-nano` (with bounded
session: 185,677 vs 118,610 = 1.57×; native resume: 326,671 vs 190,578 =
1.71×). The terser model is the better SLM — a bigger SLM is counterproductive
when its output is re-injected.

**Pure-subscription vs hybrid.** Running both the SLM and the agent on one
ChatGPT/Codex subscription (zero API keys) works, but it pins the SLM to
`gpt-5.4-mini`, whose bulkier output erodes the codex win: with a bounded session
it lands at **1.71× cheaper than vanilla** (185,677 vs 317,079) — versus **2.67×**
for the terse-`gpt-5.4-nano` hybrid (118,610 vs 317,079). The hybrid — a terse SLM
on a cheap API key plus the agent on the subscription — is materially better. See
[Hybrid Mode](https://github.com/steyangdot/PromptPilot/wiki/Hybrid-Mode).

> All figures: chain task fixing a seeded auth bug in httpx, N=5, uncached input
> tokens/run, claude-code 2.1.163 / codex-cli 0.130.0. End-state was parity —
> every config fixed the bug; the token differences carry no quality tradeoff.
> Codex dollar figures are notional.

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
