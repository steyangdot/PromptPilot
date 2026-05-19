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
- `docs/PROJECT_OVERVIEW.md` → `Project-Overview.md`
- `QUICKSTART.md` → `Quickstart.md`
- `docs/AUTHENTICATION_AND_PROVIDERS.md` → `Authentication-and-Providers.md`
- `docs/HYBRID_MODE.md` → `Hybrid-Mode.md`
- `docs/TOOL_OUTPUT_COMPRESSION.md` → `Tool-Output-Compression.md`
- `docs/TROUBLESHOOTING.md` → `Troubleshooting.md`
- `docs/ARCHITECTURE.md` → `Architecture.md`
- `docs/SLM_HARNESS.md` → `SLM-Harness.md`
- `docs/ROUTES_AND_DECISIONS.md` → `Routes-and-Decisions.md`
- `docs/SEMANTIC_PRESERVATION.md` → `Semantic-Preservation.md`
- `docs/SAFETY_MODEL.md` → `Safety-Model.md`
- `docs/TELEMETRY_AND_REPLAY.md` → `Telemetry-and-Replay.md`
- `docs/BENCHMARKS.md` → `Benchmarks.md`
- `docs/COMPARISON.md` → `Comparison.md`
- `docs/FAQ.md` → `FAQ.md`
- `docs/ROADMAP.md` → `Roadmap.md`
- `docs/WIKI_WORKFLOW.md` → `Wiki-Publishing.md`

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
- Treat the markdown files in this repository as the source of truth.
- Avoid editing the temporary wiki clone directly unless you are intentionally recovering a failed publish.
- Extend both this mapping and `scripts/publish_wiki.sh` when adding a new wiki page.
