# Troubleshooting

This page collects common setup and runtime issues.

## CLI is not installed

Run the bootstrap once, then `doctor` for re-checks:

```bash
python quickstart.py     # if you cloned the repo
prpt setup               # if you `pip install`ed PromptPilot
prpt doctor              # re-run checks any time, no install side effects
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

`handoff.md` must contain all five canonical sections:

- Goal
- Decisions made
- Files touched
- Open items
- Constraints

Header matching is case-insensitive and accepts common variants (e.g. `Files
modified`, `Decisions`, `Next steps`, `Guardrails`), so light hand-editing is
fine. Removing or omitting a section will still fail; restore the missing one
and retry.

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
