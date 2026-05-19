# Authentication and Providers

PromptPilot can run with subscription-authenticated CLIs, API keys, or a hybrid of both. You only need one working path to start.

## Provider paths

| Path | Setup | Best for |
|---|---|---|
| Claude Max/Pro subscription | `claude auth login --claudeai` | Claude users who want minimal setup |
| ChatGPT/Codex subscription | `codex login` | Codex users who want minimal setup |
| Anthropic API key | `ANTHROPIC_API_KEY=...` in `.env` | Faster SDK calls and predictable per-call billing |
| OpenAI API key | `OPENAI_API_KEY=...` in `.env` | Fast small-model calls via SDK |
| Hybrid | API key for SLM + subscription CLI for agent | Higher-volume use where SLM cost matters |

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
- `PROMPTPILOT_JUDGE` to choose the small-model judge path.
- `CLAUDE_MODEL` to choose Claude CLI model behavior.
- `USE_MAX_AUTH=1` for Max OAuth-backed harness paths where supported.

## Security notes

- Keep API keys in `.env` or your shell environment, not in committed files.
- Prefer repo-local examples such as `.env.example` for documentation.
- If a shell environment variable shadows `.env`, PromptPilot should report that clearly.

## Related pages

- [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart)
- [Troubleshooting](https://github.com/steyangdot/PromptPilot/wiki/Troubleshooting)
- [Safety Model](https://github.com/steyangdot/PromptPilot/wiki/Safety-Model)
