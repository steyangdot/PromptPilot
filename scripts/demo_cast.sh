#!/usr/bin/env bash
# Staged command sequence for recording the README demo cast.
#
# This is the script you run *inside* a recorder (asciinema, etc.) so the
# resulting GIF is reproducible. It drives the zero-setup offline demo, which
# needs no API key or coding-agent auth, so anyone can record it. See
# docs/RECORDING.md for the full record -> GIF pipeline.
#
# Usage:
#   asciinema rec demo.cast --command "bash scripts/demo_cast.sh"
# or just run it to preview the pacing:
#   scripts/demo_cast.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Pacing knobs (seconds). Bump for a slower, more readable recording.
TYPE_PAUSE="${TYPE_PAUSE:-1.0}"
READ_PAUSE="${READ_PAUSE:-2.5}"

run() {
  printf '\033[1;32m$\033[0m \033[1m%s\033[0m\n' "$*"
  sleep "$TYPE_PAUSE"
  "$@"
  sleep "$READ_PAUSE"
}

clear
printf '\033[1;36m# PromptPilot — SLM control layer for AI coding agents\033[0m\n'
printf '\033[2m# zero setup: no API key, no agent install, no network\033[0m\n\n'
sleep "$READ_PAUSE"

# 1. A precise request: constraints get pinned before the agent runs.
run python examples/demo.py --only 1

# 2. A vague request: ambiguities get flagged instead of guessed at.
run python examples/demo.py --only 2

# Optional: a real run against the coding agent (needs your auth + costs money).
# Uncomment to include live agent output in the recording:
# run prpt --dry-run "refactor the retry logic, keep the public API stable"

printf '\n\033[1;32m# Add --slm (with an API key or subscription) for the live small-model rewrite.\033[0m\n'
sleep "$READ_PAUSE"
