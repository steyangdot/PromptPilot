"""Adapter factory — selects the right downstream adapter from --tool flag.

Default --tool is "auto": probe PATH for `claude` then `codex` and pick
whichever is available. Aliases (`claude` <- `claude-code`, `openai` <- `codex`
for SDK use only) keep the surface small while staying compatible with
how users naturally type tool names.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from typing import Optional

from prpt.core.constants import DEFAULT_OPENAI_TARGET_MODEL, DEFAULT_TARGET_MODEL
from prpt.core.utils import write_stderr
from prpt.adapters.echo import EchoAdapter, ToolAdapter
from prpt.adapters.shell import CodexAdapter, ShellToolAdapter, resolve_executable_name


# Canonical -> aliases. Used both for normalizing user-provided --tool values
# and for auto-detection in PATH (the canonical name is what we probe).
_TOOL_ALIASES = {
    "claude-code": ("claude", "claude-code"),
    "codex":       ("codex",),
    "anthropic":   ("anthropic",),    # SDK path - no CLI to probe
    "openai":      ("openai",),       # SDK path - no CLI to probe
    "echo":        ("echo",),
}


def _normalize_tool(tool: str) -> str:
    """Map user-provided tool name to canonical form.

    `claude` -> `claude-code` (the CLI binary name)
    Anything else is returned lowercased and untouched.
    """
    t = (tool or "").lower().strip()
    if t == "claude":
        return "claude-code"
    return t


def _autodetect_tool() -> Optional[str]:
    """Return the canonical name of a coding-agent CLI present in PATH, or None.

    Prefers `claude` over `codex` when both are available; this matches the
    default-judge preference (Max OAuth comes first in the auto-detect order).
    """
    if shutil.which("claude") or shutil.which("claude.cmd"):
        return "claude-code"
    if shutil.which("codex") or shutil.which("codex.cmd"):
        return "codex"
    return None


def _print_missing_cli(tool: str) -> None:
    """Print actionable install hints when a coding-agent CLI is missing."""
    write_stderr(
        "[promptpilot] '{0}' CLI not found in PATH.\n"
        "  Install it before running prpt against it:\n".format(tool)
    )
    if tool == "claude-code":
        write_stderr(
            "    npm install -g @anthropic-ai/claude-code\n"
            "    (then: claude auth login --claudeai)\n"
        )
    elif tool == "codex":
        write_stderr(
            "    npm install -g @openai/codex\n"
            "    (then: codex login)\n"
        )
    else:
        write_stderr("    Check the tool's documentation for install instructions.\n")
    write_stderr("  Or run `prpt doctor` to diagnose your setup.\n")


class AdapterFactory:
    @staticmethod
    def create(args: argparse.Namespace) -> ToolAdapter:
        tool_raw = (getattr(args, "tool", None) or "auto").lower()
        extra_args = getattr(args, "tool_arg", None) or []
        model = getattr(args, "model", None) or DEFAULT_TARGET_MODEL
        max_tokens = getattr(args, "max_tokens", 4096) or 4096
        api_key = getattr(args, "api_key", None)

        if tool_raw == "auto":
            detected = _autodetect_tool()
            if detected is None:
                write_stderr(
                    "[promptpilot] --tool=auto but neither `claude` nor `codex` is in PATH.\n"
                    "  Falling back to --tool=echo (prints the optimized prompt only).\n"
                    "  Install a coding agent and re-run, or pass --tool explicitly:\n"
                )
                _print_missing_cli("claude-code")
                tool = "echo"
            else:
                tool = detected
                if getattr(args, "verbose", False):
                    write_stderr("[promptpilot] --tool=auto resolved to {0}\n".format(tool))
        else:
            tool = _normalize_tool(tool_raw)

        # Update args.tool so downstream logic (output suffix, logging) sees
        # the resolved canonical name rather than "auto".
        args.tool = tool

        if tool == "echo":
            return EchoAdapter()

        if tool == "codex":
            if not (shutil.which("codex") or shutil.which("codex.cmd")):
                _print_missing_cli("codex")
            return CodexAdapter(extra_args=extra_args)

        if tool == "anthropic":
            from prpt.adapters.anthropic_adapter import AnthropicDirectAdapter
            system        = getattr(args, "system_prompt", None)
            context_block = getattr(args, "context_block", None)
            return AnthropicDirectAdapter(
                model=model, api_key=api_key, max_tokens=max_tokens,
                system=system, context_block=context_block,
            )

        if tool == "openai":
            from prpt.adapters.openai_adapter import OpenAIDirectAdapter
            openai_model = getattr(args, "model", None) or DEFAULT_OPENAI_TARGET_MODEL
            return OpenAIDirectAdapter(model=openai_model, api_key=api_key, max_tokens=max_tokens)

        # claude-code or any other shell CLI
        if tool == "claude-code" and not (shutil.which("claude") or shutil.which("claude.cmd")):
            _print_missing_cli("claude-code")
        shell_name = "claude" if tool == "claude-code" else tool
        return ShellToolAdapter(tool_name=shell_name, extra_args=extra_args)
