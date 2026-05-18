"""promptpilot — prompt-optimizing wrapper for AI coding CLIs."""
from __future__ import annotations

from promptpilot.core.types import (
    Confidence,
    NormalizedRequest,
    RepoMetadata,
    RewriteMode,
    TaskType,
    TokenStats,
    ValidationResult,
)
from promptpilot.normalizers.base import Normalizer, SemanticValidator, create_normalizer
from promptpilot.normalizers.heuristic import HeuristicNormalizer
from promptpilot.repo.collector import RepoContextCollector
from promptpilot.cli import main, main_cli

__all__ = [
    "Confidence",
    "HeuristicNormalizer",
    "NormalizedRequest",
    "Normalizer",
    "RepoContextCollector",
    "RepoMetadata",
    "RewriteMode",
    "SemanticValidator",
    "TaskType",
    "TokenStats",
    "ValidationResult",
    "create_normalizer",
    "main",
    "main_cli",
]
