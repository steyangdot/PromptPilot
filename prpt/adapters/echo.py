"""Echo adapter — prints prompt to stdout (dry-run / default)."""
from __future__ import annotations

import argparse


class ToolAdapter:
    """Base class for all downstream tool adapters."""
    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        raise NotImplementedError


class EchoAdapter(ToolAdapter):
    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        print(final_prompt)
        return 0
