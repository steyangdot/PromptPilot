"""Judge abstraction + SLM-call primitives.

Public API:
- ``get_default_judge()`` — factory that picks Max OAuth, Anthropic API, or
  OpenAI API based on ``PROMPTPILOT_JUDGE`` env or auto-detection. Use this for
  all new code.
- ``Judge``, ``MaxHaikuJudge``, ``AnthropicApiJudge``, ``OpenAiJudge`` —
  concrete classes if you want to instantiate explicitly.
- ``extract_json(text)`` — fail-soft JSON parser for SLM outputs.
- ``judge_via_max(prompt)`` — direct Max-Haiku subprocess call (kept for
  backward compat; prefer get_default_judge() for new code).

This module was originally named ``promptpilot.harness`` (born inside a scrapped
chain5 evaluator experiment, see session summary 2026-05-06). Renamed to
``prpt.judges`` because "harness" is a misnomer for production code that
ships with the package.
"""
from prpt.judges.slm import judge_via_max, extract_json
from prpt.judges.judge import (
    Judge, MaxHaikuJudge, CodexCliJudge, AnthropicApiJudge, OpenAiJudge,
    get_default_judge,
)

__all__ = [
    "judge_via_max", "extract_json",
    "Judge", "MaxHaikuJudge", "CodexCliJudge", "AnthropicApiJudge", "OpenAiJudge",
    "get_default_judge",
]
