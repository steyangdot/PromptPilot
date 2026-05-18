# PromptPilot quickstart

Five-minute onboarding for PromptPilot, an SLM-powered control plane for AI coding agents. The SLM manages workflow decisions around Codex/Claude-style agents; the frontier model still writes and debugs the code. For all flags and env vars, see `prpt --help` and [README.md](README.md).

## 1. Run the setup script

```bash
python quickstart.py
```

Six checks (Python ≥3.9, agent CLI, install, PromptPilot CLI, auth, smoke test).
Idempotent. On failure each step prints the exact fix command. When you see
green `Setup complete`, skip to **§3 First run**.

## 2. Authentication — pick ONE path

Any one of the four standalone paths below works on its own. You do **not**
need to set up multiple. Hybrid setups (last row) are an optional advanced
optimization for users who happen to have both auths.

| Path | Setup | Powers SLM + LLM | When to pick |
|---|---|---|---|
| **Max subscription only** | `claude auth login --claudeai` | Both via Max OAuth subprocess | You have a Max/Pro subscription, don't want to manage API keys |
| **ChatGPT subscription only** | `codex login` | Both via `codex exec` subprocess | You have a ChatGPT subscription, don't want to manage API keys |
| **Anthropic API key only** | `echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env` | Both via SDK, per-call billing | You want fastest path, no subscription, predictable per-call $ |
| **OpenAI API key only** | `echo 'OPENAI_API_KEY=sk-proj-...' > .env` | Both via SDK, per-call billing | Same, OpenAI ecosystem |
| **Advanced — hybrid** (optional) | Either API key + matching subscription | SLM via API, LLM via subscription | High-volume work; want SLM speed/cost + LLM free incremental |

Quick picks:
- **Max subscription only:** `claude auth login --claudeai`. Done. Calls bill against subscription quota (doubled by Anthropic for Pro/Max in 2026). One-time informational note prints when subscription routing fires (we don't touch the OAuth token; see README "Compliance posture").
- **ChatGPT subscription only:** `codex login`. Then `prpt --tool codex "..."`. `CodexCliJudge` powers the SLM via `codex exec -m gpt-5.4-mini`. Caveat: each SLM call burns ~19k tokens of subscription quota due to codex's agent-loop overhead — fine for interactive use, heavy for chain experiments. Upgrade to the hybrid setup below if quota becomes a concern.
- **API-billed:** drop a key in `.env`. Faster (prompt caching), predictable per-call cost.
- **Advanced — hybrid for codex users:** `OPENAI_API_KEY` + `codex login`, then `prpt --normalizer slm-openai --tool codex "..."`. SLM on cheap API path (gpt-5.4-nano, ~$0.0001/call); LLM free incremental on ChatGPT subscription. ~100× cheaper per SLM call than codex-only.
- **Advanced — hybrid for claude users:** `ANTHROPIC_API_KEY` + `claude auth login --claudeai`, then `prpt --normalizer slm-anthropic --tool claude-code "..."`. SLM on cheap SDK Haiku (~$0.001/call, prompt-cached); LLM free incremental on Max. Smaller speedup than codex case but reduces subscription-routing surface area to one layer.

Full breakdown in **README → Pick a setup**.

## 3. First run

```bash
cd /path/to/your/repo
prpt --dry-run "fix the flaky test in payments"            # preview only
prpt --tool claude-code "fix the flaky test in payments"   # or --tool codex
```

Each call records a turn, so follow-ups pick up context automatically:

```bash
prpt --tool claude-code "now add a unit test for that fix"
```

## 4. Handoff / restart workflow

Sessions get heavy after many turns. Collapse to a markdown summary and resume fresh:

```bash
prpt restart                       # snapshot to ./handoff.md, clear, bootstrap
prpt restart --to docs/sess.md     # custom path
```

To curate the summary by hand:

```bash
prpt checkpoint      # writes ./handoff.md, session preserved
$EDITOR handoff.md   # tweak anything Haiku missed
prpt bootstrap       # clears session, re-populates from handoff.md
```

`handoff.md` has five required headers — `Goal`, `Decisions made`, `Files
touched`, `Open items`, `Constraints`. `bootstrap` validates them, so don't
rename or remove headers when editing.

Cost: `checkpoint`/`restart` ≈ $0.0001–$0.01 (3–7s). `bootstrap` is regex-only (~$0).

## 5. Common errors

| Error | Fix |
|---|---|
| `Not logged in · Please run /login` | `claude auth login --claudeai` |
| `checkpoint failed: No session found for <path>` | Run a regular `prpt "..."` first |
| `handoff.md missing required sections` | Restore the five canonical headers exactly |
| `[dotenv] WARNING: shell environment shadows .env value` | `unset ANTHROPIC_API_KEY` (or accept that the shell value wins) |
| `PROMPTPILOT_JUDGE=openai but OPENAI_API_KEY is not set` | Set the key in `.env`, or pick a different judge |

## Where to look next

- `prpt --help` — every flag (`--tool`, `--normalizer`, `--dry-run`, `--high-stakes`, ...)
- Env knobs: `CLAUDE_MODEL` (opus/sonnet/haiku), `PROMPTPILOT_JUDGE` (max/anthropic/openai), `USE_MAX_AUTH=1` (chain harness — uses Max OAuth instead of `--bare`). See [README.md](README.md) for compliance posture on subscription-routed paths.
- [README.md](README.md) — install + usage examples
- [SECURITY.md](SECURITY.md) — API key handling
- `prpt install-hook` — wire into Claude Code as a UserPromptSubmit hook
- `prpt stats --last 10` — review recent runs and savings (includes tool-output compression telemetry from `~/.promptpilot/compress_stats.jsonl`)
- **Tool-output compression** — the PostToolUse hook auto-shrinks noisy pytest/grep/git output. Wired in `.codex/hooks.json` for codex; add a `PostToolUse` hooks block to `.claude/settings.json` to enable for Claude Code (see README "Tool-output compression" section).
