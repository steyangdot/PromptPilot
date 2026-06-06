# PromptPilot quickstart

Five-minute onboarding for PromptPilot, an SLM-powered control plane for AI coding agents. The SLM manages workflow decisions around Codex/Claude-style agents; the frontier model still writes and debugs the code. For all flags and env vars, see `prpt --help` and the [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview).

## 1. Run setup

Pick whichever fits how you installed PromptPilot:

```bash
python quickstart.py     # if you cloned the repo
prpt setup               # if you ran `pip install prpt[claude]` / `[codex]` / `[all]`
```

Both run the same six checks (Python ≥3.9, agent CLI, install, PromptPilot CLI,
auth, smoke test). Idempotent. On failure each step prints the exact fix
command. When you see green `Setup complete`, skip to **§3 First run**.

To re-check later without reinstalling:

```bash
prpt doctor              # checks only, no install side effects
```

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
prpt --dry-run "fix the flaky test in payments"   # preview only
prpt "fix the flaky test in payments"             # auto-detects claude or codex from PATH
```

Whatever auth you picked above, the default `slm` normalizer uses the v2 control
plane (`slm-anthropic-v2` / `slm-openai-v2` for API keys, `slm-subscription-v2`
for Max OAuth / ChatGPT): a vague prompt like `prpt "make checkout faster"` is
routed to **`clarify`** — PromptPilot prints one focused question and exits
instead of guessing, so you refine and re-run. Precise prompts route straight to
`act`. (See the [demo](README.md#demo) for the full clarify → rewrite flow.)

> **Heads-up on edits:** `prpt "fix ..."` forwards the brief to the agent in one non-interactive pass, and in that mode **neither agent writes files by default** — **Claude Code** *proposes* edits, **Codex** runs *read-only*. To apply changes, add the auto-approve flag: Claude → `--tool-arg=--permission-mode --tool-arg=acceptEdits`; Codex → `--tool-arg=--full-auto`. Or run `prpt install-hook` (see §"Where to look next") to optimize prompts *inside* an interactive Claude Code / Codex session.

Each call records a turn, so follow-ups pick up context automatically:

```bash
prpt "now add a unit test for that fix"
```

Override the auto-detection when you have both installed:

```bash
prpt --tool codex "add dark mode"        # canonical names: claude-code, codex
prpt --tool claude "fix auth"            # `claude` is an alias for claude-code
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

`handoff.md` has five required sections — `Goal`, `Decisions made`, `Files
touched`, `Open items`, `Constraints`. `bootstrap` matches them
case-insensitively and accepts common variants (e.g. `Files modified`,
`Decisions`, `Next steps`), so light hand-editing is fine. Don't drop a
section entirely.

Cost: `checkpoint`/`restart` ≈ $0.0001–$0.01 (3–7s). `bootstrap` is regex-only (~$0).

## 5. Common errors

| Error | Fix |
|---|---|
| `Not logged in · Please run /login` | `claude auth login --claudeai` |
| `checkpoint failed: No session found for <path>` | Run a regular `prpt "..."` first |
| `handoff.md missing required sections` | Restore the five canonical sections; variants of each name are accepted (case-insensitive) |
| `[dotenv] WARNING: shell environment shadows .env value` | `unset ANTHROPIC_API_KEY` (or accept that the shell value wins) |
| `PROMPTPILOT_JUDGE=openai but OPENAI_API_KEY is not set` | Set the key in `.env`, or pick a different judge |
| `error: unknown option '---'` (or `prpt` seems to hang / print nothing on a question) | Upgrade: `pip install -U prpt` (>=0.2.2). On older versions, work around with `prpt --tool-arg=-- "..."`. |

## Where to look next

- `prpt --help` — the curated flag set (`--tool`, `--normalizer`, `--dry-run`, `--high-stakes`, ...)
- `prpt --advanced-help` (or `-H`) — researcher/internal flags hidden from the main help
- Env knobs (used by the `prpt` CLI): `PROMPTPILOT_JUDGE` (max/codex/anthropic/openai — forces a judge backend), `PROMPTPILOT_LET_SLM_ANSWER=1` (opt into the SLM-answer dialog on explain prompts), `PROMPTPILOT_V2_RAW_LOG=1` (log each v2 SLM raw JSON response to `~/.promptpilot/v2_slm_raw.jsonl`). See [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers) for provider setup notes.
- Inspect the v2 routing decision: `prpt --show-spec "..."` prints the parsed `ExecutionSpec` (route, target_files, risk, memory_record) to stderr. The spec is internal — only the rewritten `downstream_prompt` is forwarded to the agent — so this is how you see the JSON. Note: `CLAUDE_MODEL` and `USE_MAX_AUTH` appear in the chain-harness scripts under `research/` only — they have no effect on a regular `prpt` invocation.
- [Project Overview](https://github.com/steyangdot/PromptPilot/wiki/Project-Overview) — what PromptPilot is for
- [SECURITY.md](https://github.com/steyangdot/PromptPilot/blob/main/SECURITY.md) — API key handling
- `prpt install-hook` — wire into Claude Code (or `prpt install-hook --tool codex` for Codex)
- `prpt stats --last 10` — review recent runs and savings; see [Telemetry and Replay](https://github.com/steyangdot/PromptPilot/wiki/Telemetry-and-Replay)
- [Tool Output Compression](https://github.com/steyangdot/PromptPilot/wiki/Tool-Output-Compression) — how noisy pytest/grep/git output is compressed before the coding agent sees it
- [Troubleshooting](https://github.com/steyangdot/PromptPilot/wiki/Troubleshooting) — common setup and runtime fixes
