# Hybrid mode (API-key SLM + subscription LLM)

PromptPilot has three model layers in every call. **Hybrid mode** is the
pattern of routing each layer to the auth that fits its job:

- **SLM normalizer** — runs every call, does many small classification / rewrite operations. Route through an **API key** (Haiku or GPT-5.4-nano) — fast, prompt-cached, predictable per-call cost.
- **Judge** — used for `prpt checkpoint` / `restart` / `bootstrap`. Route through whatever's cheapest given how often you handoff.
- **Downstream coding agent** — actually writes/edits code. Route through a **subscription CLI** (`claude-code` or `codex`) — pays for itself if you use a coding agent daily.

## What this achieves — measured in tokens

Tokens are the honest, provider-neutral unit: they're measured directly and don't
depend on which API rates or subscription tier you assume. A real 15-turn coding
chain on `httpx`, by input-token footprint:

| Layer | Input tokens | What it did | Routes to |
|---|---:|---|---|
| **SLM control layer** | **~24k** | rewrote every prompt, managed session, ran every call | cheap metered API |
| **Coding agent** | **~12.66M** | the actual code-writing (268 tool calls) | your flat-fee subscription |

The control layer is **~0.2% of the token footprint** — nearly free to add. It
frames what the agent does (decides what the agent sees, resolves references,
keeps context bounded), but it doesn't generate the other 99.8% — codex's own
agent loop produces those tokens.
That's the hybrid split: route the tiny, frequent control layer to pay-per-token
API (predictable, uncapped) and the heavy agent tokens to a subscription you
already pay a flat fee for.

Two wins, both in tokens (no pricing assumptions):

1. **Cheap control** — ~24k tokens of SLM work direct ~12.66M tokens of agent work.
2. **Leaner agent runs** — the bounded session stops the agent's own token
   consumption from ballooning: the same multi-turn work runs on **~7.6× fewer
   input tokens** than the tool's native `--resume`/`exec resume` session (1.11M
   vs 8.42M, codex chain1 N=5 — see
   [Session Memory](https://github.com/steyangdot/PromptPilot/wiki/Session-Memory)).
   Native session re-feeds a growing transcript (465k→2.36M across 5 turns);
   PromptPilot stays flat (~44k/turn).

### Translating tokens to money (downstream, assumption-laden)

What those tokens *cost* depends entirely on your auth, which is why we lead with
tokens, not dollars:

- **Metered API:** you pay per token. At gpt-5.5 list rates the ~12.66M agent
  input tokens would be ~$38; the ~24k SLM tokens were ~$0.0085 of real API spend.
- **Subscription:** a flat monthly fee covers the agent tokens, drawing **finite
  quota** rather than per-token charges. Within quota the agent tokens are
  marginal-free; beyond it (sustained CI/batch) the quota runs out — we hit the
  ChatGPT usage limit mid-experiment in May 2026, so use the API path for
  high-volume automation.

The token counts above are the measured fact; the dollar figures are one
translation of them under specific rate/quota assumptions. Pitch the tokens.

## Why route the SLM to a cheap API key (v0.3.1)

The hybrid split has a second edge that the v0.3.1 `chain_auth` experiments
examined (N=5, end-state = passing `tests/test_auth.py`): **where you route the
SLM is the lever you control.** Routing it to an API key keeps SLM spend metered
and predictable and avoids the CLI subprocess overhead of an all-on-subscription
run. (Whether the *choice* of SLM model — smaller vs larger — also changes the
agent's token footprint is a separate question that hasn't been cleanly measured;
see below and [Measurement methodology](MEASUREMENT_METHODOLOGY.md).)

- **Transport is agent-uncached-invariant.** The same SLM model produces the
  same downstream agent footprint whether the SLM ran via an API SDK call or a
  subscription CLI subprocess (Haiku via API 67,501 vs via Max 65,608 — ~3%,
  equal). Transport only changes **where the SLM cost lands** ($ on a metered
  API key vs quota on a flat subscription) and adds **~20k tokens/call of CLI
  overhead** on the subprocess path. So routing the SLM to the API key is purely
  upside: same agent cost, no subprocess overhead, predictable metered spend.
- **SLM model size — token efficiency not cleanly measured.** Whether a smaller
  SLM (gpt-5.4-nano) feeds the agent fewer tokens than a bigger one (gpt-5.4-mini)
  is **unverified**: the earlier 1.57×/1.71× figures were cross-run *uncached*
  comparisons confounded by different cache warmth (the nano run hit ~89–94% vs
  the mini run ~78–90%) and different normalizers/transport, so they don't isolate
  model size. On cache-independent *total* tokens the mini runs actually fed
  slightly fewer tokens, so the data does **not** support "nano is terser /
  bigger-SLM is counterproductive." A clean interleaved same-run comparison is
  needed before claiming either way — see
  [Measurement methodology](MEASUREMENT_METHODOLOGY.md).
- **Pure-subscription works but pays real overhead.** Putting *both* the SLM and
  the agent on the ChatGPT/Max subscription (zero API keys) runs, but it pays the
  **~20k-tokens/call CLI subprocess overhead** for the SLM and gives you quota
  draw instead of predictable metered dollars. The hybrid — SLM on a cheap API
  key + agent on the subscription — avoids that overhead and keeps the SLM spend
  metered and predictable, which is why it's the recommended split.
- **End-state is parity** across every config: each one fixed the seeded bug,
  so the token differences carry **no quality tradeoff**.

The default SLM differs per backend: OpenAI API → gpt-5.4-nano, codex/ChatGPT
subscription → gpt-5.4-mini, Anthropic API and Max → claude-haiku-4-5. For an
autonomous/agent run on codex, the nano-on-API-key hybrid keeps SLM spend metered
and skips the ~20k-tokens/call CLI overhead of the mini-on-subscription path
(whether the model choice itself changes the agent's token footprint is
unverified — see above).

> **Autonomous guard (v0.3.1):** the terse nano SLM over-clarifies — on an
> actionable imperative it mis-fired the `clarify` route **20/20** (gpt-5.4-mini
> 0/20, claude-haiku-4-5 0). An autonomous agent has no human to answer a
> clarifying question, so it answered instead of acting — a silent end-state
> failure (slm_native scored 2/5 pre-fix). Setting `PROMPTPILOT_AUTONOMOUS=1`
> degrades a `clarify` route back to `act`, restoring end-state to **5/5**.
> Interactive CLI behavior is unchanged (the degrade is off by default; a human
> still sees the question). Keep this guard on for autonomous/agent use of the
> terse SLM.

## When hybrid pays off

Use hybrid when:

- You already have an active Max or ChatGPT subscription you want to amortize over coding work.
- You run `prpt` many times per day (the SLM-normalizer cost adds up — keep it API-cheap and uncapped).
- You don't want subscription quota burned on frequent classification calls.

Skip hybrid (use single-auth) when:

- You're trying PromptPilot for the first time — start with one auth path.
- You only run `prpt` occasionally (a few times per week) — the SDK-vs-subscription split doesn't matter at that volume.
- You're doing high-volume automation (e.g. CI loops, batch processing). Run the **API-key SDK path on both layers** — the Feb 2026 "ordinary use" framing in Anthropic's ToS is interpretive for sustained programmatic subscription use. See [Comparison &rarr; Compliance posture](https://github.com/steyangdot/PromptPilot/wiki/Comparison#compliance-posture-vs-openclaw--opencode).

## The three layers and how they're picked

| Layer | What it does | Selected by | Auto-detect order |
|---|---|---|---|
| **Normalizer** | Classifies intent, decides route, rewrites prompt | `--normalizer` (default `slm`) | `ANTHROPIC_API_KEY → slm-anthropic-v2 > OPENAI_API_KEY → slm-openai-v2 > Max OAuth → slm-subscription-v2` ([prpt/normalizers/base.py#L277](https://github.com/steyangdot/PromptPilot/blob/main/prpt/normalizers/base.py#L277)) |
| **Judge** | Writes/reads `handoff.md` for checkpoint and restart | `PROMPTPILOT_JUDGE` env | `max > codex > anthropic > openai` ([prpt/judges/judge.py#L372](https://github.com/steyangdot/PromptPilot/blob/main/prpt/judges/judge.py#L372)) |
| **Downstream agent** | Invokes the coding agent | `--tool` (default `auto`) | `claude > codex` ([prpt/adapters/factory.py#L42](https://github.com/steyangdot/PromptPilot/blob/main/prpt/adapters/factory.py#L42)) |

The two auto-detect orders are intentionally **opposite** on subscription
preference. The normalizer prefers API keys (SDK is faster and prompt-cached);
the judge prefers subscriptions (handoff calls are rare and the credits are
already paid for). That mismatch is precisely what makes hybrid "just work"
when both auths are configured — you don't have to set anything extra.

## Setup

You need both auth paths configured. After that, hybrid is automatic.

### 1. Set up the subscription side

Pick the agent CLI that matches your subscription:

```bash
# Claude Max / Pro subscription -> claude-code
npm install -g @anthropic-ai/claude-code
claude auth login --claudeai

# OR ChatGPT subscription -> codex
npm install -g @openai/codex
codex login
```

### 2. Add an API key

Drop it in a project-local `.env` (gitignored):

```bash
cd /path/to/your/repo
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env   # cheapest SLM (Haiku, prompt-cached)
# or, if you have OpenAI:
echo 'OPENAI_API_KEY=sk-proj-...' > .env     # SLM via gpt-5.4-nano
```

### 3. Verify the split

Run `prpt doctor` and look at sections 5 and 6 — it should report **both** a
logged-in subscription (`Max OAuth: logged in` or `codex login: Logged in`)
and an API key (`ANTHROPIC_API_KEY set` or `OPENAI_API_KEY set`).

```bash
prpt doctor
```

## Usage

### Default (auto-detect — recommended)

```bash
prpt "fix the flaky payment test"
```

With both auths configured, this picks:
- Normalizer = `slm-anthropic-v2` (uses `ANTHROPIC_API_KEY`) or `slm-openai-v2` (uses `OPENAI_API_KEY`) — the v2 JSON-spec normalizers the auto-detect now prefers (legacy v1 `slm-anthropic` / `slm-openai` remain selectable)
- Downstream agent = `claude-code` (uses Max subscription) or `codex` (uses ChatGPT subscription), whichever the auto-detect finds first

No flags needed. The model layers self-segregate.

### Explicit overrides

If you want to force the split deliberately:

```bash
# Force the API-key normalizer regardless of what auto-detect would pick
# (slm-anthropic-v2 is the v2 default; use slm-anthropic for the legacy v1 prose path)
prpt --normalizer slm-anthropic-v2 "fix the flaky payment test"

# Force subscription-based downstream agent
prpt --tool claude-code "fix the flaky payment test"
prpt --tool codex      "fix the flaky payment test"

# Pin the judge backend independently (used for checkpoint/restart)
PROMPTPILOT_JUDGE=max  prpt checkpoint   # write handoff.md via Max subscription
PROMPTPILOT_JUDGE=anthropic prpt restart # use Anthropic API key for the judge
```

### Mixed-vendor hybrid (Anthropic API key + ChatGPT subscription)

This is the pattern with the **lowest marginal cost** if you have both (subject to
the subscription's fixed monthly fee + finite quota — see the headline caveat above):

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env   # Haiku SLM, cheap and fast
codex login                                  # GPT-5.4 downstream via subscription

prpt --tool codex "refactor the auth middleware"
```

The Haiku normalizer rewrites the prompt for ~$0.0001; codex does the heavy
coding work and bills against your ChatGPT subscription quota instead of
OpenAI API per-call billing.

For the leanest codex run specifically, route the SLM to an **OpenAI API key**,
add a bounded session, and keep the autonomous guard on
(`PROMPTPILOT_AUTONOMOUS=1`). On the cache-independent headline metric, that
config measured **~3.8× fewer total tokens than vanilla** (with_session 1,224,729
vs builtin 4,664,958 total tokens/run; one interleaved codex `chain_auth` N=5
run). As a separate metric, full-price *uncached* tokens measured
**~1.86× fewer** (same-run with_session 170,264 vs builtin 317,079, observed
~93%/86% cache hit) — this is cache-warmth-sensitive (a single interleaved data
point; cross-run it varies), not a range running up to the 3.8× total ratio.
What makes the hybrid the recommended split here is putting the SLM
on the API key: it avoids the ~20k-tokens/call CLI subprocess overhead of an
all-on-subscription run and keeps SLM spend metered and predictable. See
[Measurement methodology](MEASUREMENT_METHODOLOGY.md) for why uncached ratios
depend on cache warmth and why total tokens is the headline metric.

### Python configuration

If you're embedding PromptPilot rather than using the CLI, the same three
layers are independently constructable:

```python
import os
from prpt.normalizers.base import create_normalizer
from prpt.judges import get_default_judge, AnthropicApiJudge, MaxHaikuJudge

# 1. Normalizer: explicit SDK path (API key); v2 = JSON spec + routing
normalizer = create_normalizer("slm-anthropic-v2", api_key=os.environ["ANTHROPIC_API_KEY"])

# 2. Judge: subscription (for checkpoint/restart)
os.environ["PROMPTPILOT_JUDGE"] = "max"  # forces MaxHaikuJudge
judge = get_default_judge()
# or instantiate directly:
# judge = MaxHaikuJudge()

# 3. Downstream agent: pick subscription CLI by passing --tool to the CLI,
#    or invoke `claude` / `codex` directly through ShellToolAdapter / CodexAdapter
#    when embedding. See prpt/adapters/factory.py for the wiring.
```

## Gotchas

### Codex judge has ~19k token shadow overhead per call

`CodexCliJudge` ([prpt/judges/judge.py#L10-L13](https://github.com/steyangdot/PromptPilot/blob/main/prpt/judges/judge.py#L10-L13)) spins up
codex's full agent loop for every judge call, which costs ~19,000 input tokens
of shadow quota per invocation regardless of how short your prompt is. The
`MaxHaikuJudge` strips this overhead with `--tools ""` and is much lighter.
If you're doing handoffs frequently, prefer `PROMPTPILOT_JUDGE=max` (or set
up Max OAuth) over codex for the judge specifically — even in an otherwise
codex-centric hybrid setup.

### `--normalizer slm` auto-detect prefers API keys

If you've configured both API key and subscription, the default `slm`
normalizer picks the **API key** path
([prpt/normalizers/base.py#L282-L289](https://github.com/steyangdot/PromptPilot/blob/main/prpt/normalizers/base.py#L282-L289)). This is
intentional: SDK is faster and prompt-cached. If you want the normalizer to
route through your subscription instead, pass `--normalizer slm-subscription-v2`
explicitly, or unset the API key in `.env`.

### Auto-detect is a fallback, not a requirement

You don't have to configure all four auths to use hybrid. Pick any two: one
API key + one subscription. The auto-detect picks among what's there. The
`get_default_judge` docstring spells this out
([prpt/judges/judge.py#L374-L378](https://github.com/steyangdot/PromptPilot/blob/main/prpt/judges/judge.py#L374-L378)).

### Fallback when subscription auth fails

If the subscription side stops working mid-session (token expired, network
issue, agent CLI uninstalled), the downstream `--tool` call will fail loudly
with a non-zero exit code. The SLM normalizer keeps working from the API key
either way, so you can fall back to `--tool anthropic` (SDK direct) or
`--tool openai` (SDK direct) for the coding work too — that gives a fully
API-keyed run with no subscription dependency.

### High-volume automation

For sustained programmatic use (chain experiments, CI, batch loops), the
conservative call is **API-keyed on both layers** rather than hybrid. The
Feb 2026 "ordinary use" framing in Anthropic's subscription clarification is
interpretive for high-volume programmatic CLI use — light interactive hybrid
is clearly fine; running 60 SLM calls per minute in a loop is not. See
[Comparison &rarr; Compliance posture](https://github.com/steyangdot/PromptPilot/wiki/Comparison#compliance-posture-vs-openclaw--opencode).

---

**See also:** [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers) · [Comparison](https://github.com/steyangdot/PromptPilot/wiki/Comparison) · [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks) · [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart)
