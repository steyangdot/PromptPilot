"""PromptPilot — prompt-optimizing wrapper for AI coding CLIs.

The core path is ``create_normalizer("slm")`` — auto-detects an SLM backend
(Anthropic API, OpenAI API, or subscription via Max OAuth / ChatGPT) and
returns a normalizer that rewrites prompts before they hit expensive models.
``HeuristicNormalizer`` is the no-auth fallback.

Common entry points:

- ``create_normalizer("slm")`` — recommended default; auto-detects backend
- ``create_normalizer("heuristic")`` — rule-based, no API/auth needed
- ``SubscriptionSLMNormalizer`` — Max OAuth / ChatGPT subscription path
- ``OpenAISLMNormalizer``, ``SLMNormalizer`` (Anthropic) — direct SDK paths
- ``get_default_judge()`` — pick a judge (subscription > API key) for
  custom SLM workflows outside the normalizer protocol
"""
from __future__ import annotations

from prpt.core.types import (
    Confidence,
    NormalizedRequest,
    RepoMetadata,
    RewriteMode,
    TaskType,
    TokenStats,
    ValidationResult,
)
from prpt.normalizers.base import (
    Normalizer,
    SemanticValidator,
    build_final_downstream_prompt,
    build_output_suffix,
    create_normalizer,
)
from prpt.normalizers.heuristic import HeuristicNormalizer
from prpt.repo.collector import RepoContextCollector
from prpt.cli import main, main_cli

# SLM normalizers — exposed at the top level since the SLM path is the
# product's core feature, not a power-user opt-in. Each import is wrapped
# so missing optional deps (anthropic / openai SDK) don't break the
# top-level `import prpt`.
try:
    from prpt.normalizers.slm_subscription import SubscriptionSLMNormalizer
except (ImportError, RuntimeError):
    SubscriptionSLMNormalizer = None  # type: ignore[assignment]

try:
    from prpt.normalizers.slm_anthropic import SLMNormalizer as AnthropicSLMNormalizer
except ImportError:
    AnthropicSLMNormalizer = None  # type: ignore[assignment]

try:
    from prpt.normalizers.slm_openai import OpenAISLMNormalizer
except ImportError:
    OpenAISLMNormalizer = None  # type: ignore[assignment]

try:
    from prpt.normalizers.slm_openai_v2 import OpenAISLMNormalizerV2
except ImportError:
    OpenAISLMNormalizerV2 = None  # type: ignore[assignment]

# Judges — re-exported so users building custom SLM workflows can grab the
# default judge or a specific backend without reaching into prpt.judges.
from prpt.judges import (  # noqa: E402
    Judge,
    MaxHaikuJudge,
    AnthropicApiJudge,
    OpenAiJudge,
    CodexCliJudge,
    get_default_judge,
    extract_json,
)

__all__ = [
    # Types
    "Confidence",
    "NormalizedRequest",
    "RepoMetadata",
    "RewriteMode",
    "TaskType",
    "TokenStats",
    "ValidationResult",
    # Normalizer protocol + factory
    "Normalizer",
    "SemanticValidator",
    "create_normalizer",
    "build_final_downstream_prompt",
    "build_output_suffix",
    # Normalizers (concrete)
    "HeuristicNormalizer",
    "SubscriptionSLMNormalizer",
    "AnthropicSLMNormalizer",
    "OpenAISLMNormalizer",
    "OpenAISLMNormalizerV2",
    # Judges
    "Judge",
    "MaxHaikuJudge",
    "AnthropicApiJudge",
    "OpenAiJudge",
    "CodexCliJudge",
    "get_default_judge",
    "extract_json",
    # Repo / CLI
    "RepoContextCollector",
    "main",
    "main_cli",
]
