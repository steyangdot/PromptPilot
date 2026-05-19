# PromptPilot quickstart

Five-minute onboarding for PromptPilot, an SLM-powered control plane for AI coding agents. The SLM manages workflow decisions around Codex/Claude-style agents; the frontier model still writes and debugs the code. For all flags and env vars, see `prpt --help` and the [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview).

## 1. Run the setup script

```bash
python quickstart.py
```

Six checks (Python ≥3.9, agent CLI, install, PromptPilot CLI, auth, smoke test).
Idempotent. On failure each step prints the exact fix command. When you see
green `Setup complete`, skip to **§3 First run**.

## 2. Authentication — pick ONE path

Any one of these works on its own:

| Path | Setup | Best for |
|---|---|---|
| Max subscription | `claude auth login --claudeai` | Claude users who want zero API-key setup |
| ChatGPT subscription | `codex login` | Codex users who want zero API-key setup |
| Anthropic API key | `echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env` | Fast SDK path with per-call billing |
| OpenAI API key | `echo 'OPENAI_API_KEY=sk-proj-...' > .env` | Fast SDK path with per-call billing |
| Hybrid (optional) | API key + matching subscription | High-volume usage (cheap SLM + subscription LLM) |

For detailed tradeoffs, quota notes, and provider-specific setup guidance, see [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers).

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
- Env knobs: `CLAUDE_MODEL` (opus/sonnet/haiku), `PROMPTPILOT_JUDGE` (max/anthropic/openai), `USE_MAX_AUTH=1` (chain harness uses Max OAuth instead of `--bare`). See [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers) for provider setup notes.
- [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview) — what PromptPilot is for
- [SECURITY.md](https://github.com/steyangdot/PromptPilot/blob/main/SECURITY.md) — API key handling
- `prpt install-hook` — wire into Claude Code as a UserPromptSubmit hook
- `prpt stats --last 10` — review recent runs and savings; see [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
- [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression) — how noisy pytest/grep/git output is compressed before the coding agent sees it
- [Troubleshooting](https://github.com/steyangdot/PromptPilot/wiki/Troubleshooting) — common setup and runtime fixes
