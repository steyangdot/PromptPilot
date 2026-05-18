#!/usr/bin/env python3
"""
Compatibility shim — the monolith has been split into the ``promptpilot`` package.

This file re-exports the public names that older code (e.g. hooks) may import.
New code should ``import promptpilot`` or ``from promptpilot.…`` directly.
"""
from __future__ import annotations

import sys
import warnings

warnings.warn(
    "promptpilot_mvp.py is deprecated. Use 'import promptpilot' or 'from promptpilot.…' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything the old monolith exposed
from prpt.core.types import (  # noqa: F401
    Confidence,
    NormalizedRequest,
    RepoMetadata,
    RewriteMode,
    TaskType,
    TokenStats,
    ValidationResult,
)
from prpt.core.constants import (  # noqa: F401
    DEFAULT_LOG_FILE,
    DEFAULT_SLM_ANTHROPIC,
    DEFAULT_TARGET_MODEL,
    HARD_CONSTRAINT_HINTS,
    HELP_TEXT,
    MODEL_PRICING,
    PROTECTED_PATTERNS,
    REQUESTED_OUTPUT_HINTS,
)
from prpt.normalizers.base import (  # noqa: F401
    Normalizer,
    SemanticValidator,
    build_final_downstream_prompt,
    build_structured_prompt,
    create_normalizer,
)
from prpt.normalizers.heuristic import HeuristicNormalizer  # noqa: F401
from prpt.repo.collector import RepoContextCollector  # noqa: F401
from prpt.adapters.echo import EchoAdapter, ToolAdapter  # noqa: F401
from prpt.adapters.factory import AdapterFactory  # noqa: F401
from prpt.cli import main, main_cli  # noqa: F401

# Feature-detection flags that old hook code checked
try:
    from prpt.normalizers.slm_anthropic import SLMNormalizer  # noqa: F401
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from prpt.normalizers.slm_openai import OpenAISLMNormalizer  # noqa: F401
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


if __name__ == "__main__":
    main_cli()
