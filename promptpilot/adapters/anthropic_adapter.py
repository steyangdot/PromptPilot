"""Direct Anthropic API adapter — calls a model and captures actual token usage.

Supports claude-opus-4-7, claude-sonnet-4-7, and any future Anthropic model
via the `model` constructor parameter.

Prompt-caching design
---------------------
The adapter accepts an optional `system` string (a stable instruction block)
and an optional `context_block` string (stable repo context).  When either is
large enough to cross Anthropic's minimum cacheable threshold, a
`cache_control: ephemeral` marker is inserted so repeated calls within the
5-minute TTL window are served from cache at 0.10× the normal input price.

Cache breakpoint placement:
    [ system (cacheable) ] [ context_block (cacheable) ] | [ task prompt ]
    └────────── stable prefix, written once ────────────┘   └── dynamic ──┘

When no system / context_block is provided the adapter falls back to the
original single-string call — fully backward-compatible.
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from promptpilot.core.constants import (
    DEFAULT_TARGET_MODEL, CACHE_WRITE_MULTIPLIER, CACHE_READ_MULTIPLIER,
    MODEL_PRICING,
)
from promptpilot.core.utils import write_stderr
from promptpilot.adapters.echo import ToolAdapter

# Anthropic's minimum cacheable block: ~1 024 tokens for Sonnet/Opus.
# Using chars as a cheap proxy: 1 024 tokens ≈ 4 096 chars.
_CACHE_MIN_CHARS = 4_096
_EPHEMERAL = {"type": "ephemeral"}


def _extract_usage(response) -> dict:
    """Pull all Anthropic usage fields into a flat dict (cache-aware)."""
    u = response.usage
    return {
        "input_tokens":                 getattr(u, "input_tokens",                 0) or 0,
        "output_tokens":                getattr(u, "output_tokens",                0) or 0,
        "cache_creation_input_tokens":  getattr(u, "cache_creation_input_tokens",  0) or 0,
        "cache_read_input_tokens":      getattr(u, "cache_read_input_tokens",       0) or 0,
    }


def _cost_usd(usage: dict, model: str) -> float:
    """Compute total cost in USD using Anthropic cache-aware pricing."""
    price = MODEL_PRICING.get(model, {"input": 15.00, "output": 75.00})
    p_in  = price["input"]  / 1_000_000
    p_out = price["output"] / 1_000_000
    return (
        usage["input_tokens"]               * p_in
        + usage["output_tokens"]            * p_out
        + usage["cache_creation_input_tokens"] * p_in * CACHE_WRITE_MULTIPLIER
        + usage["cache_read_input_tokens"]     * p_in * CACHE_READ_MULTIPLIER
    )


class AnthropicDirectAdapter(ToolAdapter):
    """
    Calls an Anthropic model (default: claude-opus-4-7) via the SDK.

    Parameters
    ----------
    model : str
        Any Anthropic model ID, e.g. ``"claude-opus-4-7"`` or
        ``"claude-sonnet-4-7"``.
    system : str, optional
        Stable instruction block sent as the ``system`` message.  When large
        enough, marked with ``cache_control: ephemeral`` so it is cached
        across repeated calls within 5 minutes.
    context_block : str, optional
        Stable repo-context blob.  Inserted as the first user content block,
        also marked cacheable when large enough.  Placed *before* the task
        prompt so the cache breakpoint sits between the stable prefix and the
        per-request tail.
    api_key : str, optional
        Falls back to ``ANTHROPIC_API_KEY`` env var.
    max_tokens : int
        Maximum output tokens (default 4 096).
    """

    def __init__(
        self,
        model: str = DEFAULT_TARGET_MODEL,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        system: Optional[str] = None,
        context_block: Optional[str] = None,
    ):
        try:
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
            )
        except ImportError:
            raise ImportError(
                "Claude tool support requires the anthropic SDK.\n"
                "  Run: pip install promptpilot[claude]"
            )
        self._model         = model
        self._max_tokens    = max_tokens
        self._system        = system
        self._context_block = context_block
        self.last_usage: Optional[dict] = None

    # ------------------------------------------------------------------
    # Cache-aware request builders
    # ------------------------------------------------------------------

    def _build_system(self) -> list[dict] | str | None:
        """Return system param: block-list with cache marker if large enough."""
        if not self._system:
            return None
        block: dict = {"type": "text", "text": self._system}
        if len(self._system) >= _CACHE_MIN_CHARS:
            block["cache_control"] = _EPHEMERAL
        return [block]

    def _build_user_content(self, task_prompt: str) -> list[dict] | str:
        """
        Build user content.

        If a context_block was provided: split into two blocks —
          [0] cacheable context, [1] fresh task prompt.
        Otherwise: single plain-string (backward-compat, no cache marker).
        """
        if not self._context_block:
            return task_prompt

        ctx_block: dict = {
            "type": "text",
            "text": (
                "<repository_context>\n"
                + self._context_block
                + "\n</repository_context>"
            ),
        }
        if len(self._context_block) >= _CACHE_MIN_CHARS:
            ctx_block["cache_control"] = _EPHEMERAL

        return [
            ctx_block,
            {"type": "text", "text": task_prompt},
        ]

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        self.last_usage = None
        if getattr(args, "verbose", False):
            cache_state = "on" if self._system or self._context_block else "off"
            write_stderr(
                "[adapter] calling {model} via Anthropic SDK "
                "(cache={cache})".format(model=self._model, cache=cache_state)
            )
        try:
            create_kwargs: dict = {
                "model":      self._model,
                "max_tokens": self._max_tokens,
                "messages":   [{
                    "role":    "user",
                    "content": self._build_user_content(final_prompt),
                }],
            }
            system_arg = self._build_system()
            if system_arg is not None:
                create_kwargs["system"] = system_arg

            response = self._client.messages.create(**create_kwargs)

            usage = _extract_usage(response)
            usage["total_cost_usd"] = _cost_usd(usage, self._model)
            self.last_usage = usage

            verbose = getattr(args, "verbose", False)
            printed_text = False
            tool_uses: list[str] = []

            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "text" or (btype is None and hasattr(block, "text")):
                    text = getattr(block, "text", "")
                    if text:
                        print(text)
                        printed_text = True
                elif btype == "thinking":
                    if verbose:
                        thinking_text = getattr(block, "thinking", "")
                        write_stderr("[adapter] thinking: {0}".format(thinking_text))
                elif btype == "redacted_thinking":
                    if verbose:
                        write_stderr("[adapter] thinking: <redacted>")
                elif btype == "tool_use":
                    name = getattr(block, "name", "<unknown>")
                    tool_uses.append(name)

            if verbose and tool_uses:
                write_stderr(
                    "[adapter] tool_use blocks: {0}".format(", ".join(tool_uses))
                )

            if not printed_text:
                stop_reason = getattr(response, "stop_reason", "unknown")
                detail = (
                    " (tool_use: {0})".format(", ".join(tool_uses))
                    if tool_uses else ""
                )
                write_stderr(
                    "[adapter] warning: no text content in response "
                    "(stop_reason={0}){1}".format(stop_reason, detail)
                )
            return 0

        except Exception as exc:
            write_stderr("[adapter] Anthropic API call failed: {0}".format(exc))
            return 1

    # ------------------------------------------------------------------
    # Convenience: human-readable cache summary for --verbose / stats
    # ------------------------------------------------------------------

    def cache_summary(self) -> str:
        """Return a one-line cache hit summary from the last run."""
        if not self.last_usage:
            return "no run yet"
        r  = self.last_usage.get("cache_read_input_tokens",  0)
        w  = self.last_usage.get("cache_creation_input_tokens", 0)
        total_cached = r + w
        if total_cached == 0:
            return "cache: off (no cacheable blocks)"
        hit_pct = r / total_cached * 100 if total_cached else 0
        return (
            "cache: {r:,} read / {w:,} written  ({pct:.0f}% hit)  "
            "cost ${cost:.6f}".format(
                r=r, w=w, pct=hit_pct,
                cost=self.last_usage.get("total_cost_usd", 0.0),
            )
        )
