#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook.

Intercepts every user prompt, rewrites it with Claude Haiku (grounded in repo
file contents), then injects the optimized version as additionalContext so the
expensive downstream model gets a precise, unambiguous task description.

Fails open on every error — a broken hook must never block Claude Code.
"""
from __future__ import annotations

import json
import os
import sys

# Resolve project root (two levels up from .claude/hooks/)
_HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HOOK_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Minimum prompt word count worth optimizing
_MIN_WORDS = 4

# Prompts that are clearly slash commands or meta — skip them
_SKIP_PREFIXES = ("/", "#", "!")


def _allow(additional_context: str | None = None) -> None:
    """Exit 0 with optional additionalContext."""
    if additional_context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }))
    sys.exit(0)


def main() -> None:
    # --- 1. Parse hook payload ---
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        _allow()

    prompt: str = payload.get("prompt", "").strip()
    cwd: str = payload.get("cwd", os.getcwd())

    # --- 2. Early-exit conditions ---
    if not prompt:
        _allow()
    if prompt.startswith(_SKIP_PREFIXES):
        _allow()
    if len(prompt.split()) < _MIN_WORDS:
        _allow()

    # --- 3. Import promptpilot components ---
    try:
        from prpt.normalizers.base import create_normalizer
        from prpt.repo.collector import RepoContextCollector
    except ImportError:
        _allow()  # promptpilot package not available

    # --- 4. Rewrite with SLM (auto-detects Haiku or GPT-5.4-nano) ---
    try:
        normalizer = create_normalizer("slm", load_repo_content=True)
    except (ImportError, RuntimeError):
        _allow()  # no SLM backend available

    try:
        repo = RepoContextCollector().collect(cwd)
        normalized = normalizer.normalize(prompt, repo)
        rewritten = normalized.normalized_prompt.strip()
    except Exception:
        _allow()

    # No-op if SLM returned the same text (or failed silently)
    if not rewritten or rewritten == prompt:
        _allow()

    context = (
        "[promptpilot] SLM-optimized task interpretation:\n\n"
        "{rewritten}\n\n"
        "Use this as a precise guide. "
        "If it conflicts with the user's original message, follow the original."
    ).format(rewritten=rewritten)

    _allow(context)


if __name__ == "__main__":
    main()
