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
copy_page "README.md" "Project-Overview.md"
copy_page "QUICKSTART.md" "Quickstart.md"

cat > "$workdir/_Sidebar.md" <<'SIDEBAR'
- [Home](Home)
- [Project Overview](Project-Overview)
- [Quickstart](Quickstart)
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
