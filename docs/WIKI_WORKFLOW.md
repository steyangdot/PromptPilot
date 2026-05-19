# Wiki publishing workflow

PromptPilot can publish selected docs to the GitHub Wiki using:

```bash
scripts/publish_wiki.sh
```

## What it does

- Derives the wiki remote from `origin` (or uses `--remote` / `WIKI_REMOTE_URL`)
- Clones (or updates) the wiki repo (`*.wiki.git`)
- Copies curated docs into wiki pages
- Writes a minimal `_Sidebar.md`
- Commits and pushes wiki changes only when there is a diff

## Current page mapping

- `docs/README.md` → `Home.md`
- `README.md` → `Project-Overview.md`
- `QUICKSTART.md` → `Quickstart.md`

## Usage

```bash
# Normal publish
scripts/publish_wiki.sh

# Preview only (no commit/push)
scripts/publish_wiki.sh --dry-run

# Explicit remote override
scripts/publish_wiki.sh --remote git@github.com:steyangdot/PromptPilot.wiki.git
```

## Notes

- The wiki is a separate git repository from the main code repo.
- You must have push access to `<repo>.wiki.git` for publish to succeed.
- This workflow is intentionally lightweight and can be extended with additional page mappings.
