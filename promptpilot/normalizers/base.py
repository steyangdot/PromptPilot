"""Normalizer ABC, SemanticValidator, prompt builders, and create_normalizer factory."""
from __future__ import annotations

import os
from typing import List, Optional

from promptpilot.core.types import (
    NormalizedRequest, RepoMetadata, RewriteMode, ValidationResult,
)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class Normalizer:
    def normalize(
        self, prompt: str, repo: RepoMetadata, high_stakes: bool = False,
    ) -> NormalizedRequest:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Semantic validator
# ---------------------------------------------------------------------------

class SemanticValidator:
    def validate(self, normalized: NormalizedRequest) -> ValidationResult:
        issues: List[str] = []
        original_lower = normalized.original_prompt.lower()

        for constraint in normalized.hard_constraints:
            if constraint.lower() not in original_lower:
                issues.append(
                    "Hard constraint may not be verbatim-preserved: {0}".format(constraint)
                )

        if normalized.rewrite_mode == RewriteMode.EXTRACT_ONLY.value:
            if not normalized.normalized_prompt.startswith("Original user request:"):
                issues.append(
                    "Heuristic normalized prompt does not begin with the original user request block."
                )

        if issues:
            return ValidationResult("uncertain", issues, "review")
        return ValidationResult("pass", [], "pass_through")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_structured_prompt(
    *,
    original_prompt: str,
    objective: str,
    explicit_context: List[str],
    hard_constraints: List[str],
    soft_preferences: List[str],
    requested_output: List[str],
    protected_spans: List[str],
    ambiguities: List[str],
    assumptions: List[str],
) -> str:
    def section(title: str, items: List[str], fallback: str = "None explicitly provided.") -> str:
        if not items:
            return "{0}:\n- {1}".format(title, fallback)
        return "{0}:\n{1}".format(title, "\n".join("- {0}".format(i) for i in items))

    parts = [
        "Original user request:", original_prompt, "",
        "Structured interpretation:", "",
        "Objective:", objective, "",
        section("Explicit context", explicit_context), "",
        section("Hard constraints", hard_constraints), "",
        section("Soft preferences", soft_preferences), "",
        section("Requested output", requested_output), "",
        section("Protected spans", protected_spans), "",
        section("Ambiguities", ambiguities), "",
        section("Assumptions added by wrapper", assumptions), "",
        "Instruction to downstream model:",
        "If there is any conflict between the structured interpretation and the original "
        "user request, follow the original user request.",
    ]
    return "\n".join(parts)


_TARGET_FILES_HINT_MAX = 8       # cap files listed in the hint
_TARGET_FILES_HINT_ENV  = "PROMPTPILOT_USE_TARGET_HINT"


def _format_target_files_hint(target_files: Optional[List[str]]) -> str:
    """Format the v2 spec.target_files into a compact downstream prompt line.

    Returns an empty string when target_files is missing/empty OR when the
    PROMPTPILOT_USE_TARGET_HINT env var is not set to "1". The env-var gate
    is the A/B switch for the v2 roadmap #5 benchmark: spec already emits
    target_files; consuming them is opt-in until the canary proves a win.

    Output format (concise to stay friendly with cacheable prompt prefixes):
      [likely files: a.py, b.py, c.py]
    Caps the list at 8 entries with an overflow marker, matching the
    truncation discipline used elsewhere (e.g. assistant session records).
    """
    if os.environ.get(_TARGET_FILES_HINT_ENV) != "1":
        return ""
    files = [f for f in (target_files or []) if isinstance(f, str) and f.strip()]
    if not files:
        return ""
    shown = files[:_TARGET_FILES_HINT_MAX]
    suffix = ""
    if len(files) > _TARGET_FILES_HINT_MAX:
        suffix = ", ... (+{0} more)".format(len(files) - _TARGET_FILES_HINT_MAX)
    return "\n[likely files: {0}{1}]".format(", ".join(shown), suffix)


def build_final_downstream_prompt(
    normalized: NormalizedRequest,
    repo: RepoMetadata,
    target_files: Optional[List[str]] = None,
) -> str:
    if normalized.rewrite_mode == RewriteMode.EXTRACT_PLUS_LIGHT_REWRITE.value:
        return _build_slm_downstream_prompt(normalized, repo, target_files)
    return _build_heuristic_downstream_prompt(normalized, repo)


def _build_slm_downstream_prompt(
    normalized: NormalizedRequest,
    repo: RepoMetadata,
    target_files: Optional[List[str]] = None,
) -> str:
    ctx_parts: List[str] = []
    if repo.dominant_language:
        ctx_parts.append(repo.dominant_language)
    if repo.branch:
        ctx_parts.append("branch={0}".format(repo.branch))
    if repo.test_framework:
        ctx_parts.append("tests={0}".format(repo.test_framework))
    if repo.changed_files:
        ctx_parts.append("changed: " + ", ".join(repo.changed_files[:10]))

    context_line = "; ".join(ctx_parts)
    hint = _format_target_files_hint(target_files)
    if context_line:
        return "{0}\n[cwd={1}; {2}]{3}".format(
            normalized.normalized_prompt, repo.cwd, context_line, hint,
        )
    return "{0}\n[cwd={1}]{2}".format(normalized.normalized_prompt, repo.cwd, hint)


def _build_heuristic_downstream_prompt(normalized: NormalizedRequest, repo: RepoMetadata) -> str:
    runtime_context = [
        "Current working directory: {0}".format(repo.cwd),
        "Current branch: {0}".format(repo.branch or "unknown"),
        "Dominant language: {0}".format(repo.dominant_language or "unknown"),
        "Test framework: {0}".format(repo.test_framework or "unknown"),
    ]
    if repo.changed_files:
        runtime_context.append("Changed files: " + ", ".join(repo.changed_files[:20]))
    else:
        runtime_context.append("Changed files: none detected")

    guidance = [
        "Execution guidance:",
        "- Be conservative with protected spans and hard constraints.",
        "- Do not infer away ambiguities.",
        "- If a required decision depends on an ambiguity, state it explicitly.",
        "- Prefer the smallest safe change when the request is underspecified.",
    ]

    return (
        normalized.normalized_prompt
        + "\n\nAdditional runtime context:\n"
        + "\n".join("- {0}".format(item) for item in runtime_context)
        + "\n\n"
        + "\n".join(guidance)
    )


# ---------------------------------------------------------------------------
# Output format constraints
# ---------------------------------------------------------------------------

_OUTPUT_CONSTRAINTS: dict = {
    "anthropic": {
        "pinpoint": (
            "Output constraints:\n"
            "- Use the Edit tool, not Write. Only use Write for new files.\n"
            "- Change only the specific lines required. No surrounding refactoring.\n"
            "- No preamble or post-edit explanation."
        ),
        "localized": (
            "Output constraints:\n"
            "- Prefer the Edit tool over Write for existing files.\n"
            "- Keep any post-edit explanation to one sentence maximum."
        ),
    },
    "codex": {
        "pinpoint": (
            "Output constraints:\n"
            "- Change only the specific lines specified. Do not rewrite the whole file.\n"
            "- No preamble, no post-change summary."
        ),
        "localized": (
            "Output constraints:\n"
            "- Avoid full file rewrites for existing files where possible.\n"
            "- Keep any explanation brief."
        ),
    },
    "_default": {
        "pinpoint": (
            "Output constraints:\n"
            "- Make only the minimal targeted change. No surrounding refactoring.\n"
            "- No preamble or post-change summary."
        ),
        "localized": (
            "Output constraints:\n"
            "- Keep changes focused. Avoid unnecessary rewrites.\n"
            "- Keep any explanation brief."
        ),
    },
}

# Tool name aliases → canonical key
_TOOL_ALIASES: dict = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude-code": "anthropic",
    "codex": "codex",
}


def build_output_suffix(scope: Optional[str], tool: str) -> str:
    """Return output format constraint text to append to the final prompt.

    Only applies to act-intent tasks with pinpoint or localized scope.
    Returns empty string for broad/new scopes (let the model decide).
    """
    if scope not in ("pinpoint", "localized"):
        return ""
    canonical_tool = _TOOL_ALIASES.get((tool or "").lower(), "_default")
    table = _OUTPUT_CONSTRAINTS.get(canonical_tool, _OUTPUT_CONSTRAINTS["_default"])
    return table.get(scope, "")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_normalizer(
    name: str,
    api_key: Optional[str] = None,
    load_repo_content: bool = True,
) -> Normalizer:
    selected = (name or "heuristic").strip().lower()

    if selected == "heuristic":
        from promptpilot.normalizers.heuristic import HeuristicNormalizer
        return HeuristicNormalizer()

    if selected == "slm-anthropic":
        from promptpilot.normalizers.slm_anthropic import SLMNormalizer
        return SLMNormalizer(api_key=api_key, load_repo_content=load_repo_content)

    if selected == "slm-openai":
        from promptpilot.normalizers.slm_openai import OpenAISLMNormalizer
        return OpenAISLMNormalizer(api_key=api_key, load_repo_content=load_repo_content)

    if selected == "slm-openai-v2":
        from promptpilot.normalizers.slm_openai_v2 import OpenAISLMNormalizerV2
        return OpenAISLMNormalizerV2(api_key=api_key, load_repo_content=load_repo_content)

    if selected == "slm-subscription":
        from promptpilot.normalizers.slm_subscription import SubscriptionSLMNormalizer
        return SubscriptionSLMNormalizer(load_repo_content=load_repo_content)

    if selected == "slm":
        # Auto-detect normalizer backend.
        #
        # Priority:
        #   1. Explicit PROMPTPILOT_JUDGE -> subscription path (user already chose)
        #   2. ANTHROPIC_API_KEY      -> slm-anthropic (faster, prompt caching)
        #   3. OPENAI_API_KEY         -> slm-openai
        #   4. Max OAuth logged in    -> slm-subscription (no API charges)
        #
        # The subscription path is a fallback rather than the top choice here
        # because the SDK paths run faster and use prompt caching. If a user
        # wants subscription routing as the primary, they can pass
        # `--normalizer slm-subscription` explicitly OR set PROMPTPILOT_JUDGE=max.
        # See slm_subscription.py docstring for the compliance posture.
        anthropic_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        openai_key = api_key or os.environ.get("OPENAI_API_KEY")
        explicit_judge = os.environ.get("PROMPTPILOT_JUDGE", "").strip().lower()

        if explicit_judge in ("max", "codex", "anthropic", "openai"):
            try:
                from promptpilot.normalizers.slm_subscription import SubscriptionSLMNormalizer
                return SubscriptionSLMNormalizer(load_repo_content=load_repo_content)
            except (ImportError, RuntimeError):
                pass

        if anthropic_key:
            try:
                from promptpilot.normalizers.slm_anthropic import SLMNormalizer
                return SLMNormalizer(api_key=anthropic_key, load_repo_content=load_repo_content)
            except ImportError:
                pass

        if openai_key:
            try:
                from promptpilot.normalizers.slm_openai import OpenAISLMNormalizer
                return OpenAISLMNormalizer(api_key=openai_key, load_repo_content=load_repo_content)
            except ImportError:
                pass

        # No API keys -- fall through to subscription (Max OAuth). Succeeds if
        # `claude auth login --claudeai` has been run.
        try:
            from promptpilot.normalizers.slm_subscription import SubscriptionSLMNormalizer
            return SubscriptionSLMNormalizer(load_repo_content=load_repo_content)
        except (ImportError, RuntimeError):
            pass

        raise RuntimeError(
            "No SLM backend available. Either:\n"
            "  - Run `claude auth login --claudeai` for Max OAuth (no API key needed), or\n"
            "  - pip install promptpilot[claude] and set ANTHROPIC_API_KEY, or\n"
            "  - pip install promptpilot[codex] and set OPENAI_API_KEY"
        )

    raise ValueError(
        "Unsupported normalizer: '{0}'. Choose from: heuristic, slm, slm-anthropic, slm-openai, slm-openai-v2, slm-subscription".format(name)
    )
