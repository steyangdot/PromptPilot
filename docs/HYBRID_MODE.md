# Hybrid mode (API-key SLM + subscription LLM)

PromptPilot has three model layers in every call. **Hybrid mode** is the
pattern of routing each layer to the auth that fits its job:

- **SLM normalizer** — runs every call, does many small classification / rewrite operations. Route through an **API key** (Haiku or GPT-5.4-nano) — fast, prompt-cached, predictable per-call cost.
- **Judge** — used for `prpt checkpoint` / `restart` / `bootstrap`. Route through whatever's cheapest given how often you handoff.
- **Downstream coding agent** — actually writes/edits code. Route through a **subscription CLI** (`claude-code` or `codex`) — pays for itself if you use a coding agent daily.

The headline: pay API cents for the frequent small calls, burn subscription
credits on the expensive code-writing calls. In one measured chain5 run
(see [Benchmarks](https://github.com/steyangdot/PromptPilot/wiki/Benchmarks)),
the subsidized-vs-paid cost ratio came out around ~4,500× on the downstream
work, because the subscription absorbed the expensive part. That ratio is a
single data point, not a guarantee — your workload will land where it lands.

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
| **Normalizer** | Classifies intent, decides route, rewrites prompt | `--normalizer` (default `slm`) | `ANTHROPIC_API_KEY > OPENAI_API_KEY > Max OAuth` ([prpt/normalizers/base.py:277](prpt/normalizers/base.py:277)) |
| **Judge** | Writes/reads `handoff.md` for checkpoint and restart | `PROMPTPILOT_JUDGE` env | `max > codex > anthropic > openai` ([prpt/judges/judge.py:372](prpt/judges/judge.py:372)) |
| **Downstream agent** | Invokes the coding agent | `--tool` (default `auto`) | `claude > codex` ([prpt/adapters/factory.py:42](prpt/adapters/factory.py:42)) |

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
- Normalizer = `slm-anthropic` (uses `ANTHROPIC_API_KEY`) or `slm-openai` (uses `OPENAI_API_KEY`)
- Downstream agent = `claude-code` (uses Max subscription) or `codex` (uses ChatGPT subscription), whichever the auto-detect finds first

No flags needed. The model layers self-segregate.

### Explicit overrides

If you want to force the split deliberately:

```bash
# Force API-key normalizer regardless of what auto-detect would pick
prpt --normalizer slm-anthropic "fix the flaky payment test"

# Force subscription-based downstream agent
prpt --tool claude-code "fix the flaky payment test"
prpt --tool codex      "fix the flaky payment test"

# Pin the judge backend independently (used for checkpoint/restart)
PROMPTPILOT_JUDGE=max  prpt checkpoint   # write handoff.md via Max subscription
PROMPTPILOT_JUDGE=anthropic prpt restart # use Anthropic API key for the judge
```

### Mixed-vendor hybrid (Anthropic API key + ChatGPT subscription)

This is the pattern that hits the **highest subsidy ratio** if you have both:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env   # Haiku SLM, cheap and fast
codex login                                  # GPT-5.4 downstream via subscription

prpt --tool codex "refactor the auth middleware"
```

The Haiku normalizer rewrites the prompt for ~$0.0001; codex does the heavy
coding work and bills against your ChatGPT subscription quota instead of
OpenAI API per-call billing.

### Python configuration

If you're embedding PromptPilot rather than using the CLI, the same three
layers are independently constructable:

```python
import os
from prpt.normalizers.base import create_normalizer
from prpt.judges import get_default_judge, AnthropicApiJudge, MaxHaikuJudge

# 1. Normalizer: explicit SDK path (API key)
normalizer = create_normalizer("slm-anthropic", api_key=os.environ["ANTHROPIC_API_KEY"])

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

`CodexCliJudge` ([prpt/judges/judge.py:10-13](prpt/judges/judge.py:10)) spins up
codex's full agent loop for every judge call, which costs ~19,000 input tokens
of shadow quota per invocation regardless of how short your prompt is. The
`MaxHaikuJudge` strips this overhead with `--tools ""` and is much lighter.
If you're doing handoffs frequently, prefer `PROMPTPILOT_JUDGE=max` (or set
up Max OAuth) over codex for the judge specifically — even in an otherwise
codex-centric hybrid setup.

### `--normalizer slm` auto-detect prefers API keys

If you've configured both API key and subscription, the default `slm`
normalizer picks the **API key** path
([prpt/normalizers/base.py:282-289](prpt/normalizers/base.py:282)). This is
intentional: SDK is faster and prompt-cached. If you want the normalizer to
route through your subscription instead, pass `--normalizer slm-subscription`
explicitly, or unset the API key in `.env`.

### Auto-detect is a fallback, not a requirement

You don't have to configure all four auths to use hybrid. Pick any two: one
API key + one subscription. The auto-detect picks among what's there. The
`get_default_judge` docstring spells this out
([prpt/judges/judge.py:374-378](prpt/judges/judge.py:374)).

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
