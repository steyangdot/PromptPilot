#!/usr/bin/env bash
set -euo pipefail

# Publish selected repository docs to the GitHub Wiki repository.
#
# Usage:
#   scripts/publish_wiki.sh
#   scripts/publish_wiki.sh --dry-run
#   scripts/publish_wiki.sh --remote git@github.com:owner/repo.wiki.git
#
# Env:
#   WIKI_REMOTE_URL     Optional override for wiki git URL.
#   WIKI_WORKDIR        Optional override for temporary checkout directory.

DRY_RUN=0
REMOTE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --remote)
      REMOTE_OVERRIDE="${2:-}"
      [[ -n "$REMOTE_OVERRIDE" ]] || { echo "--remote requires a value" >&2; exit 2; }
      shift 2
      ;;
    -h|--help)
      sed -n '1,40p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

origin_url="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$origin_url" && -z "${WIKI_REMOTE_URL:-}" && -z "$REMOTE_OVERRIDE" ]]; then
  echo "Could not resolve origin URL. Pass --remote or set WIKI_REMOTE_URL." >&2
  exit 1
fi

derive_wiki_url() {
  local url="$1"
  if [[ "$url" =~ ^git@github.com:(.+)\.git$ ]]; then
    echo "git@github.com:${BASH_REMATCH[1]}.wiki.git"
  elif [[ "$url" =~ ^https://github.com/(.+)\.git$ ]]; then
    echo "https://github.com/${BASH_REMATCH[1]}.wiki.git"
  elif [[ "$url" =~ ^https://github.com/(.+)$ ]]; then
    echo "https://github.com/${BASH_REMATCH[1]}.wiki.git"
  else
    echo ""
  fi
}

wiki_remote="${REMOTE_OVERRIDE:-${WIKI_REMOTE_URL:-}}"
if [[ -z "$wiki_remote" ]]; then
  wiki_remote="$(derive_wiki_url "$origin_url")"
fi

if [[ -z "$wiki_remote" ]]; then
  echo "Unable to derive wiki remote from origin: $origin_url" >&2
  echo "Provide --remote <repo.wiki.git> or set WIKI_REMOTE_URL." >&2
  exit 1
fi

workdir="${WIKI_WORKDIR:-$(mktemp -d "${TMPDIR:-/tmp}/promptpilot-wiki.XXXXXX")}" 
cleanup=0
if [[ -z "${WIKI_WORKDIR:-}" ]]; then
  cleanup=1
fi

if [[ ! -d "$workdir/.git" ]]; then
  git clone "$wiki_remote" "$workdir"
else
  git -C "$workdir" fetch origin
  git -C "$workdir" checkout master || git -C "$workdir" checkout main
  git -C "$workdir" pull --ff-only
fi

copy_page() {
  local src="$1" dst="$2"
  if [[ ! -f "$src" ]]; then
    echo "Source file missing: $src" >&2
    exit 1
  fi
  cp "$src" "$workdir/$dst"
}

# Map in-repo docs to wiki pages.
copy_page "docs/README.md" "Home.md"
copy_page "docs/PROJECT_OVERVIEW.md" "Project-Overview.md"
copy_page "QUICKSTART.md" "Quickstart.md"
copy_page "docs/AUTHENTICATION_AND_PROVIDERS.md" "Authentication-and-Providers.md"
copy_page "docs/HYBRID_MODE.md" "Hybrid-Mode.md"
copy_page "docs/TOOL_OUTPUT_COMPRESSION.md" "Tool-Output-Compression.md"
copy_page "docs/TROUBLESHOOTING.md" "Troubleshooting.md"
copy_page "docs/ARCHITECTURE.md" "Architecture.md"
copy_page "docs/SLM_HARNESS.md" "SLM-Harness.md"
copy_page "docs/ROUTES_AND_DECISIONS.md" "Routes-and-Decisions.md"
copy_page "docs/SEMANTIC_PRESERVATION.md" "Semantic-Preservation.md"
copy_page "docs/SAFETY_MODEL.md" "Safety-Model.md"
copy_page "docs/TELEMETRY_AND_REPLAY.md" "Telemetry-and-Replay.md"
copy_page "docs/BENCHMARKS.md" "Benchmarks.md"
copy_page "docs/COMPARISON.md" "Comparison.md"
copy_page "docs/FAQ.md" "FAQ.md"
copy_page "docs/ROADMAP.md" "Roadmap.md"
copy_page "docs/WIKI_WORKFLOW.md" "Wiki-Publishing.md"

cat > "$workdir/_Sidebar.md" <<'SIDEBAR'
- [Home](Home)
- Getting Started
  - [Project Overview](Project-Overview)
  - [Quickstart](Quickstart)
  - [Authentication and Providers](Authentication-and-Providers)
  - [Hybrid Mode](Hybrid-Mode)
  - [Troubleshooting](Troubleshooting)
  - [FAQ](FAQ)
- Concepts
  - [Architecture](Architecture)
  - [SLM Harness](SLM-Harness)
  - [Routes and Decisions](Routes-and-Decisions)
  - [Semantic Preservation](Semantic-Preservation)
  - [Safety Model](Safety-Model)
- Operations
  - [Tool Output Compression](Tool-Output-Compression)
  - [Telemetry and Replay](Telemetry-and-Replay)
- Evaluation
  - [Benchmarks](Benchmarks)
  - [Comparison](Comparison)
- Project
  - [Roadmap](Roadmap)
  - [Wiki Publishing](Wiki-Publishing)
SIDEBAR

cd "$workdir"
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] Wiki changes staged but not committed/pushed:" >&2
    git --no-pager diff --cached --stat
    exit 0
  fi

  git commit -m "docs: sync wiki from repository"
  git push origin HEAD
  echo "Wiki updated successfully at: $wiki_remote"
else
  echo "No wiki changes to publish."
fi

if [[ "$cleanup" -eq 1 ]]; then
  rm -rf "$workdir"
fi
