# Troubleshooting

This page collects common setup and runtime issues.

## CLI is not installed

Run the quickstart script first:

```bash
python quickstart.py
```

If it reports a missing dependency, follow the exact command printed by the failing step.

## Not logged in

For Claude Code subscription auth:

```bash
claude auth login --claudeai
```

For Codex subscription auth:

```bash
codex login
```

For API-key auth, set the relevant key in `.env` or the shell environment.

## `.env` value is ignored

If the shell already has `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, it may shadow the value in `.env`.

Unset the shell variable or intentionally keep the shell value.

## Handoff validation fails

`handoff.md` must keep the canonical sections:

- Goal
- Decisions made
- Files touched
- Open items
- Constraints

Do not rename those headers when curating the file by hand.

## Wiki pages did not update

The wiki is a separate git repository. Editing docs in the main repo is not enough.

Run:

```bash
scripts/publish_wiki.sh
```

Use `--dry-run` first if you want to preview the wiki diff.

## Related pages

- [Quickstart](https://github.com/steyangdot/PromptPilot/wiki/Quickstart)
- [Authentication and Providers](https://github.com/steyangdot/PromptPilot/wiki/Authentication-and-Providers)
- [Wiki Publishing](https://github.com/steyangdot/PromptPilot/wiki/Wiki-Publishing)
