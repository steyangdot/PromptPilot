"""Adapter factory — selects the right downstream adapter from --tool flag."""
from __future__ import annotations

import argparse
from typing import Optional

from promptpilot.core.constants import DEFAULT_TARGET_MODEL
from promptpilot.adapters.echo import EchoAdapter, ToolAdapter
from promptpilot.adapters.shell import CodexAdapter, ShellToolAdapter


class AdapterFactory:
    @staticmethod
    def create(args: argparse.Namespace) -> ToolAdapter:
        tool = (getattr(args, "tool", None) or "echo").lower()
        extra_args = getattr(args, "tool_arg", None) or []
        model = getattr(args, "model", None) or DEFAULT_TARGET_MODEL
        max_tokens = getattr(args, "max_tokens", 4096) or 4096
        api_key = getattr(args, "api_key", None)

        if tool == "echo":
            return EchoAdapter()

        if tool == "codex":
            return CodexAdapter(extra_args=extra_args)

        if tool == "anthropic":
            from promptpilot.adapters.anthropic_adapter import AnthropicDirectAdapter
            system        = getattr(args, "system_prompt", None)
            context_block = getattr(args, "context_block", None)
            return AnthropicDirectAdapter(
                model=model, api_key=api_key, max_tokens=max_tokens,
                system=system, context_block=context_block,
            )

        if tool == "openai":
            from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter
            return OpenAIDirectAdapter(model=model, api_key=api_key, max_tokens=max_tokens)

        return ShellToolAdapter(tool_name=tool, extra_args=extra_args)
