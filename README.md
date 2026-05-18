# PromptPilot

Prompt-optimizing wrapper for AI coding CLIs. Uses a cheap SLM (Claude Haiku or GPT-5.4-nano) to rewrite developer prompts before they hit expensive models, reducing token consumption.

> **First-time user?** Start with **[QUICKSTART.md](QUICKSTART.md)** — five-minute onboarding covering install, auth, and the `handoff.md` workflow.

## Install

Pick the extra that matches your coding agent:

```bash
pip install prpt[claude]      # for use with claude-code (Claude Haiku SLM)
pip install prpt[codex]       # for use with codex (GPT-5.4-nano SLM)
pip install prpt[all]         # both
```

`[anthropic]` / `[openai]` are kept as aliases for backward compatibility.

### Install from source

If you'd rather skip PyPI — to inspect the code, pick up unreleased fixes
on `main`, pin to a specific commit, or hack on it — you have three
equivalent options:

```bash
# 1. One-liner straight from GitHub (no clone, pins easy):
pip install "git+https://github.com/steyangdot/PromptPilot.git[all]"
pip install "git+https://github.com/steyangdot/PromptPilot.git@v0.1.0[all]"   # pin to a tag

# 2. Clone + editable install (best for development; source edits live):
git clone https://github.com/steyangdot/PromptPilot.git
cd PromptPilot
pip install -e ".[all]"

# 3. Clone + regular install:
git clone https://github.com/steyangdot/PromptPilot.git
cd PromptPilot
pip install ".[all]"
```

All three produce the same `prpt` command on PATH and the same `prpt`
import. Feature parity is exact — `pyproject.toml` is the only source of
truth for both PyPI and source builds. The from-source path is the only
way to access fixes that have landed on `main` but haven't been published
to PyPI in a tagged release yet.

## Auth: which path to pick

**Recommended for Max/Pro users:** `claude auth login --claudeai`. PromptPilot
auto-detects the OAuth session and routes the SLM via the official `claude`
CLI subprocess. No API key needed, no incremental charges — calls bill against
your subscription's monthly quota (which Anthropic doubled for Pro/Max in
2026).

**Alternative:** drop `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) into `.env`.
The SDK paths run faster (~1-2s/call vs ~5-7s for subprocess) and use prompt
caching. Billed per-call against your API credits.

### Compliance posture (worth understanding before relying on subscription routing)

Anthropic clarified on **Feb 20, 2026** that subscription OAuth credentials are
"intended exclusively to support ordinary use of Claude Code and other native
Anthropic applications." On **Apr 4, 2026**, technical enforcement landed
against third-party tools (OpenClaw, OpenCode, etc.) that extracted OAuth
tokens and made direct API calls while spoofing Claude Code's request shape.

**PromptPilot is structurally different from those tools:**

| Axis | OpenClaw / OpenCode | PromptPilot |
|---|---|---|
| OAuth token handling | Read `~/.claude/.credentials.json`, sent bearer directly | Never touches the token; official `claude` binary owns it |
| API endpoint | Direct calls to `api.anthropic.com/v1/messages` | No direct API calls — the binary makes them |
| Request shape | Spoofed Claude Code's headers, system prompt, tool defs | The real `claude` binary builds the request |
| Client identity | Client *replacement* impersonating Claude Code | Single local user invoking their own CLI as a child process |
| Fan-out | Shared service across many accounts | Runs locally with the user's own logged-in CLI |

When PromptPilot calls `claude -p`, Anthropic's server logs see a request from the
real `claude` binary with the real user-agent and request shape — because
that's what made the call. The literal token-clause prohibition does not apply.

**What's still interpretive:** the Feb 20 ToS phrase "ordinary use of Claude
Code" is broader than the token clause. Whether driving `claude -p` from a
programmatic loop counts as "ordinary use" depends on Anthropic's
interpretation. Interactive single-shot use is clearly ordinary; high-volume
batched chain runs are harder to characterize. The risk is much lower than
the OpenClaw pattern but not zero.

A separate monthly credit pool for Agent SDK / `claude -p` is expected
**Jun 15, 2026**, which may formalize programmatic subscription use under a
sanctioned billing model. This note will be updated then.

### On the codex / OpenAI side

`CodexCliJudge` is the symmetric path for users with a ChatGPT subscription
(`codex login`). Auto-detected after Max OAuth in the priority order
`max > codex > anthropic > openai`. Same compliance posture as MaxHaikuJudge:
we invoke the official `codex` binary; the OAuth token stays inside the
codex process.

Two operational caveats specific to codex:
1. **Heavier per-call overhead.** ~20k input tokens of subscription quota per
   call due to the agent-loop spin-up (~6.5k of which are cached) — vs
   MaxHaikuJudge's lighter `claude -p --model haiku` path. Suitable for
   light SLM use within quota; not great for high-volume chain experiments.
2. **Model name follows the codex naming scheme.** ChatGPT-auth codex
   accepts current-gen names like `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.3-codex`,
   `gpt-5.2` (and the unspecified default `gpt-5.5`). Legacy names like
   `gpt-4o-mini` return a 400. The judge defaults to `gpt-5.4-mini` — the
   smallest SLM-tier model available on ChatGPT-subscription auth. Override
   via the constructor if you have an `OPENAI_API_KEY` backing codex (no
   such restriction applies on API-keyed codex).

`OPENAI_API_KEY` remains the supported per-call API path for codex users
who want predictable, cached, per-token billing — and is the cheapest path
overall (gpt-5.4-nano at $0.20/M input via SDK vs the ~19k-token subscription
quota burn per CodexCliJudge call).

## Pick a setup

PromptPilot supports four auth paths. Any one of them works on its own — you do
**not** need to set up multiple. The list is in rough order of how much setup
work they involve.

### 1. Max subscription only (Claude users)

```bash
claude auth login --claudeai
prpt "fix the flaky test in payments"
```

No API key, no `.env` editing. SLM routes via `MaxHaikuJudge` (`claude -p
--model haiku --tools ""` subprocess); LLM routes via `claude` CLI. Both bill
against your Max subscription quota; zero incremental $.

### 2. ChatGPT subscription only (Codex users)

```bash
codex login
prpt --tool codex "fix the flaky test in payments"
```

SLM routes via `CodexCliJudge` (`codex exec -m gpt-5.4-mini` subprocess); LLM
routes via `codex` CLI. Both bill against your ChatGPT subscription. Caveat:
each SLM call consumes ~19k tokens of subscription quota due to codex's
agent-loop overhead — fine for interactive use, heavy for chain experiments.
See "Advanced: hybrid" below if this becomes a quota concern.

### 3. API key only

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env    # Claude users
# or
echo 'OPENAI_API_KEY=sk-proj-...' >> .env      # Codex users
```

Fully supported, fastest per-call (~1-2s SDK round-trips), prompt caching
active. Bills against API credits per call. Use this if you don't have a
relevant subscription or want predictable per-call billing.

### 4. Advanced: hybrid auth (API SLM + subscription LLM)

**Optional optimization.** If you have *both* a subscription *and* a
matching API key, you can route the cheap SLM work through the API and the
expensive LLM work through the subscription. This is the most
cost-efficient configuration but requires both auths to be set up.

#### Hybrid for codex / ChatGPT users

```bash
# .env:
OPENAI_API_KEY=sk-proj-...

# Plus:
codex login

# Invocation (the --normalizer slm-openai flag is required to force the SDK path):
prpt --normalizer slm-openai --tool codex "fix the flaky test in payments"
```

- **SLM normalizer** → `OPENAI_API_KEY` via SDK + `gpt-5.4-nano` ($0.20/M
  input, $1.25/M output). Each rewrite call ~$0.0001-0.0003. Fast,
  prompt-cached.
- **Downstream agent** → ChatGPT subscription via `codex exec`. Free
  incremental.

The big win over codex-subscription-for-everything: codex inflates every SLM
call to ~19k input tokens (agent-loop overhead). At gpt-5.4-mini's $0.75/M
input rate inside codex that's $0.015 real-$ equivalent / call (or 30% of
GPT-5.4 quota on the subscription side). The SDK path sends only ~400
tokens. Same SLM work, ~100× cheaper per call. Plus nano is 3.6–3.75×
cheaper than mini per token.

#### Hybrid for claude-code / Max users

```bash
# .env:
ANTHROPIC_API_KEY=sk-ant-...

# Plus:
claude auth login --claudeai

# Invocation:
prpt --normalizer slm-anthropic --tool claude-code "fix the flaky test in payments"
```

- **SLM normalizer** → `ANTHROPIC_API_KEY` via SDK + Haiku. ~$0.001/call.
  Fast, prompt-cached.
- **Downstream agent** → claude-code via Max OAuth subscription. Free
  incremental.

The gain is smaller than the codex case — `MaxHaikuJudge`'s subprocess uses
`--tools ""` which strips the agent loop, so it's ~20× more expensive than
SDK Haiku per call (not 100× like codex). Still a real win: SDK is faster
(~4s vs ~7s) and prompt caching is active. **Bonus:** moves the SLM layer
to a fully-supported API path, leaving only the LLM layer in the
subscription-routing gray zone discussed above.

#### When hybrid is worth setting up

- You're running high-volume work (chain tests, long sessions, batched edits)
  where per-call overhead compounds
- You already have an API key from your provider account (often included
  with codex/ChatGPT or Anthropic developer accounts)
- You want SLM walltime to stay sub-2-seconds per call
- You want to reduce subscription-routing surface area (claude case)

#### When standalone subscription is fine

- Light interactive use (a handful of calls per session)
- You don't want to manage an API key in `.env`
- Your subscription quota is comfortably larger than your usage

The auto-detect order is **`max > codex > anthropic > openai`** — a fallback
priority, not a setup requirement. You pick which auth(s) to configure; the
auto-detect chooses among what's available.

## Usage

```bash
prpt --dry-run "fix flaky test in payments"
prpt --normalizer slm --dry-run "refactor auth, no API changes"
prpt --normalizer slm --tool anthropic "add dark mode"
prpt --theme dark --dry-run "add dark mode"
prpt install-hook   # wire into Claude Code as a UserPromptSubmit hook
```

With `--normalizer slm-openai-v2`, the SLM can route prompts to **clarify**
(ask a question and exit), **passthrough** (run the raw prompt unmodified),
or **answer** (respond directly without invoking the agent) instead of the
default **act** path. See `prpt/core/spec.py` for the routing schema.

## Tool-output compression

PromptPilot ships a `PostToolUse` hook that intercepts `Bash` tool responses
and shrinks them before the LLM sees them. Targets the dominant token-bleed
in agent sessions: pytest tracebacks, grep floods, deep `find` results,
verbose `git diff`, linter spew, installer logs. The compressor is
command-type-aware ([prpt/compress/tool_output.py](prpt/compress/tool_output.py)).

**Install for Claude Code:**

Add a hooks block to your project-level `.claude/settings.json` (or global
`~/.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "python /path/to/PromptPilot/.claude/hooks/compress_tool_output.py",
        "timeout": 10
      }]
    }]
  }
}
```

**Install for codex:**

codex picks up `.codex/hooks.json` automatically when run inside a PromptPilot
checkout. No manual wiring needed.

**Telemetry:**

Each compression event is appended to `~/.promptpilot/compress_stats.jsonl`
(disable with `PROMPTPILOT_COMPRESS_LOG_DISABLE=1`). Surface aggregate stats
with:

```bash
prpt stats        # includes "--- Tool-output compression ---" section
```

**Kill switch:**

```bash
PROMPTPILOT_COMPRESS_DISABLE=1 claude   # bypass compression for this session
```

## Releasing (for maintainers)

PyPI publishes are automated via GitHub Actions Trusted Publishing — no
API token in the repo. To cut a release:

```bash
# 1. Bump version in pyproject.toml (semantic versioning: patch/minor/major)
# 2. Commit + push to main
git add pyproject.toml
git commit -m "Bump to 0.1.1"
git push origin main

# 3. Create a release (the publish.yml workflow fires automatically)
gh release create v0.1.1 --title "v0.1.1 — short title" --notes "release notes..."
```

The workflow at `.github/workflows/publish.yml` builds, validates, and
uploads the sdist + wheel. PyPI's Trusted Publishing exchanges the
workflow's OIDC identity for a short-lived upload token at job time —
no long-lived secrets are stored anywhere.

GitHub commits don't auto-publish — only released tags do. README polish,
typo fixes, internal refactors land on `main` without touching PyPI. The
PyPI page is refreshed only when a release ships, which keeps the
package's `pip install` story stable.

The full test suite (`.github/workflows/test.yml`) runs on every push to
`main` and on pull requests across Python 3.9/3.11/3.13.

## License

PromptPilot is licensed under the [Apache License 2.0](LICENSE).

PromptPilot wraps multiple LLM vendors' tools (Claude, Codex, OpenAI) in an
area where patent claims are unsettled, and the explicit patent grant in
Apache §3 protects both users and contributors. It keeps every permissive
freedom MIT offers (commercial use, modification, redistribution) and adds a
patent grant plus a termination clause that discourages frivolous patent
litigation. For a control-plane tool meant to slot into other people's
production setups, that protection felt like the wrong corner to cut.

### License FAQ

**Why not MIT?**
MIT is simpler and works for most projects, but has no patent provision.
Apache 2.0 explicitly grants patent rights from every contributor (§3) and
terminates the license for anyone who sues for patent infringement — useful
insurance when the legal landscape around LLM tooling is still moving.

**Can I use this commercially?**
Yes. Commercial use, modification, private use, and redistribution are all
allowed. No royalties, no copyleft. You don't owe anything back.

**What are my obligations if I redistribute it?**
Keep the `LICENSE` file with the copy, note any files you modified, and
preserve the `NOTICE` file's contents (when one exists). That's it. No
share-alike requirement.

**Can I fork and rename?**
Yes, but you can't use "PromptPilot" or related marks to imply your fork is
the official project (§6 trademark clause). Pick a new name for
substantially-changed forks.

Contributions are accepted under the same license per Apache 2.0 §5 — by
opening a PR you agree your work is contributed under Apache 2.0 unless
explicitly stated otherwise.
