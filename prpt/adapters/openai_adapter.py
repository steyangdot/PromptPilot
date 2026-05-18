"""Direct OpenAI API adapter — calls a model and captures actual token usage."""
from __future__ import annotations

import argparse
import os
from typing import Optional

from prpt.core.utils import write_stderr
from prpt.adapters.echo import ToolAdapter


class OpenAIDirectAdapter(ToolAdapter):
    """
    Calls an OpenAI model via the SDK.
    Captures actual input/output token usage for before/after comparison.
    """

    def __init__(
        self, model: str = "gpt-4o", api_key: Optional[str] = None, max_tokens: int = 4096,
    ):
        try:
            import openai as _openai
            self._client = _openai.OpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY")
            )
        except ImportError:
            raise ImportError(
                "Codex tool support requires the openai SDK.\n"
                "  Run: pip install prpt[codex]"
            )
        self._model = model
        self._max_tokens = max_tokens
        self.last_usage: Optional[dict] = None

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Return True if the model is an o-series reasoning model (o1/o3/o4/o5+)."""
        name = model.lower()
        # Strip common provider prefixes (e.g. "openai/o3-mini", "azure/o1")
        if "/" in name:
            name = name.rsplit("/", 1)[1]
        return name.startswith(("o1", "o3", "o4", "o5", "o6", "o7", "o8", "o9"))

    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        self.last_usage = None
        if getattr(args, "verbose", False):
            write_stderr("[adapter] calling {0} via OpenAI SDK".format(self._model))
        try:
            # o-series reasoning models (o1/o3/o4/...) reject `max_tokens` and
            # require `max_completion_tokens` instead.
            token_kwarg = (
                "max_completion_tokens"
                if self._is_reasoning_model(self._model)
                else "max_tokens"
            )
            create_kwargs = {
                "model": self._model,
                "messages": [{"role": "user", "content": final_prompt}],
                token_kwarg: self._max_tokens,
            }
            response = self._client.chat.completions.create(**create_kwargs)
            self.last_usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            content = response.choices[0].message.content
            if content:
                print(content)
            return 0
        except Exception as exc:
            write_stderr("[adapter] OpenAI API call failed: {0}".format(exc))
            return 1
