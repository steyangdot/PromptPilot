"""Heuristic normalizer — pure regex/keyword extraction, no API calls."""
from __future__ import annotations

import re
from typing import List

from prpt.core.constants import (
    CLAUSE_SPLIT_PATTERN, HARD_CONSTRAINT_HINTS, PREFERENCE_SPLIT_PATTERN,
    PROTECTED_PATTERNS, REQUESTED_OUTPUT_HINTS,
)
from prpt.core.types import (
    Confidence, NormalizedRequest, RepoMetadata, RewriteMode, TaskType,
)
from prpt.core.utils import unique_preserve_order
from prpt.normalizers.base import Normalizer, build_structured_prompt


class HeuristicNormalizer(Normalizer):
    def normalize(
        self, prompt: str, repo: RepoMetadata, high_stakes: bool = False,
    ) -> NormalizedRequest:
        prompt_clean = prompt.strip()
        task_type = self._detect_task_type(prompt_clean)
        protected_spans = self._extract_protected_spans(prompt_clean)
        hard_constraints = self._extract_hard_constraints(prompt_clean, protected_spans)
        requested_output = self._extract_requested_output(prompt_clean)
        explicit_context = self._extract_explicit_context(prompt_clean, repo)
        soft_preferences = self._extract_soft_preferences(
            prompt_clean, hard_constraints, requested_output
        )
        ambiguities = self._detect_ambiguities(prompt_clean, task_type, repo)
        assumptions = self._infer_low_risk_assumptions(prompt_clean, task_type, repo)
        objective = self._build_objective(prompt_clean, task_type)
        confidence = self._score_confidence(prompt_clean, ambiguities, high_stakes)
        normalized_prompt = build_structured_prompt(
            original_prompt=prompt_clean, objective=objective,
            explicit_context=explicit_context, hard_constraints=hard_constraints,
            soft_preferences=soft_preferences, requested_output=requested_output,
            protected_spans=protected_spans, ambiguities=ambiguities, assumptions=assumptions,
        )
        return NormalizedRequest(
            original_prompt=prompt_clean, task_type=task_type, objective=objective,
            explicit_context=explicit_context, hard_constraints=hard_constraints,
            soft_preferences=soft_preferences, requested_output=requested_output,
            protected_spans=protected_spans, ambiguities=ambiguities, assumptions=assumptions,
            omissions=[], confidence=confidence,
            needs_review=high_stakes or confidence == Confidence.LOW.value or len(ambiguities) >= 2,
            rewrite_mode=RewriteMode.EXTRACT_ONLY.value,
            normalized_prompt=normalized_prompt,
        )

    def _detect_task_type(self, text: str) -> str:
        lower = text.lower()
        if any(k in lower for k in ["flaky test", "test only", "generate tests", "add tests", "write tests"]):
            return TaskType.TEST_GENERATION.value
        if any(k in lower for k in ["root cause", "why is", "why does", "investigate", "debug"]):
            return TaskType.ROOT_CAUSE_ANALYSIS.value
        if any(k in lower for k in ["fix", "bug", "broken", "timeout", "failing"]):
            return TaskType.BUG_FIX.value
        if any(k in lower for k in ["refactor", "clean up"]):
            return TaskType.REFACTOR.value
        if any(k in lower for k in ["migrate", "migration", "upgrade"]):
            return TaskType.MIGRATION.value
        if any(k in lower for k in ["design", "architecture", "system design"]):
            return TaskType.ARCHITECTURE_DESIGN.value
        if any(k in lower for k in ["implement", "add feature", "build"]):
            return TaskType.FEATURE_IMPLEMENTATION.value
        if any(k in lower for k in ["explain", "walk me through"]):
            return TaskType.CODE_EXPLANATION.value
        if any(k in lower for k in ["document", "docs", "readme"]):
            return TaskType.DOCUMENTATION.value
        return TaskType.UNKNOWN.value

    def _extract_protected_spans(self, text: str) -> List[str]:
        spans: List[str] = []
        for pattern in PROTECTED_PATTERNS:
            spans.extend(m.group(0) for m in re.finditer(pattern, text, flags=re.IGNORECASE))
        spans.extend(re.findall(r"`([^`]+)`", text))
        spans.extend(re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", text))
        spans.extend(re.findall(r"\b[A-Za-z0-9_./-]+\.[A-Za-z0-9_./-]+\b", text))
        return unique_preserve_order(spans)

    def _extract_hard_constraints(self, text: str, protected_spans: List[str]) -> List[str]:
        constraints: List[str] = []
        for clause in re.split(CLAUSE_SPLIT_PATTERN, text, flags=re.IGNORECASE):
            stripped = clause.strip()
            if stripped and any(hint in stripped.lower() for hint in HARD_CONSTRAINT_HINTS):
                constraints.append(stripped)
        for span in protected_spans:
            if any(h in span.lower() for h in [
                "minimal patch", "backward compatible", "no schema", "read-only", "do not touch",
            ]):
                constraints.append(span)
        return unique_preserve_order(constraints)

    def _extract_requested_output(self, text: str) -> List[str]:
        outputs: List[str] = []
        for clause in re.split(CLAUSE_SPLIT_PATTERN, text, flags=re.IGNORECASE):
            stripped = clause.strip()
            if stripped and any(hint in stripped.lower() for hint in REQUESTED_OUTPUT_HINTS):
                outputs.append(stripped)
        return unique_preserve_order(outputs)

    def _extract_explicit_context(self, text: str, repo: RepoMetadata) -> List[str]:
        context: List[str] = []
        if repo.dominant_language:
            context.append("Repository dominant language appears to be {0}.".format(repo.dominant_language))
        if repo.test_framework:
            context.append("Detected test framework: {0}.".format(repo.test_framework))
        for item in re.findall(r"\b(?:in|for|within)\s+([A-Za-z0-9_./:-]+)", text)[:5]:
            context.append("User referenced: {0}".format(item))
        return unique_preserve_order(context)

    def _extract_soft_preferences(
        self, text: str, hard_constraints: List[str], requested_output: List[str],
    ) -> List[str]:
        preferences: List[str] = []
        hard_low = {x.lower() for x in hard_constraints}
        out_low = {x.lower() for x in requested_output}
        for clause in re.split(PREFERENCE_SPLIT_PATTERN, text):
            stripped = clause.strip()
            low = stripped.lower()
            if (
                stripped and low not in hard_low and low not in out_low
                and any(k in low for k in ["prefer", "ideally", "concise", "brief", "smallest safe"])
            ):
                preferences.append(stripped)
        return unique_preserve_order(preferences)

    def _detect_ambiguities(self, text: str, task_type: str, repo: RepoMetadata) -> List[str]:
        ambiguities: List[str] = []
        lower = text.lower()
        if task_type in {TaskType.BUG_FIX.value, TaskType.ROOT_CAUSE_ANALYSIS.value} and not self._has_target_subsystem_context(
            lower, repo
        ):
            ambiguities.append("Target subsystem is not clearly specified.")
        if task_type == TaskType.UNKNOWN.value:
            ambiguities.append("Task type is not clearly specified.")
        if "fix" in lower and not re.search(
            r"\b(error|timeout|failing|crash|bug|issue|exception)\b", lower
        ):
            ambiguities.append("Failure mode is not clearly specified.")
        if repo.dominant_language is None:
            ambiguities.append("Repository language could not be inferred.")
        return unique_preserve_order(ambiguities)

    def _infer_low_risk_assumptions(
        self, text: str, task_type: str, repo: RepoMetadata,
    ) -> List[str]:
        assumptions: List[str] = []
        lower = text.lower()
        if task_type in {TaskType.BUG_FIX.value, TaskType.ROOT_CAUSE_ANALYSIS.value} and "test" not in lower:
            assumptions.append("The user likely wants suggested tests or verification steps.")
        if task_type == TaskType.BUG_FIX.value:
            assumptions.append("The user likely wants a code-level fix rather than a high-level discussion only.")
        if repo.changed_files:
            assumptions.append("Recently changed files may be relevant to the requested task.")
        return unique_preserve_order(assumptions)

    def _has_target_subsystem_context(self, text: str, repo: RepoMetadata) -> bool:
        if re.search(
            r"\b(frontend|backend|api|service|worker|handler|db|database|test|module|package|component|controller|client)\b",
            text,
        ):
            return True

        prompt_tokens = set(re.findall(r"\b[a-z][a-z0-9_-]{2,}\b", text))
        if not prompt_tokens:
            return False

        repo_tokens = set()
        for path in repo.changed_files:
            for part in re.split(r"[\\/._-]+", path.lower()):
                normalized = part.strip()
                if len(normalized) >= 3:
                    repo_tokens.add(normalized)
                    if normalized.endswith("s") and len(normalized) >= 4:
                        repo_tokens.add(normalized[:-1])

        for token in prompt_tokens:
            singular = token[:-1] if token.endswith("s") and len(token) >= 4 else token
            if token in repo_tokens or singular in repo_tokens:
                return True

        return False

    def _build_objective(self, text: str, task_type: str) -> str:
        stripped = text.strip().rstrip(".")
        mapping = {
            TaskType.BUG_FIX.value: "Fix the reported issue conservatively.",
            TaskType.ROOT_CAUSE_ANALYSIS.value: "Analyze and explain the likely root cause.",
            TaskType.TEST_GENERATION.value: "Generate or update relevant tests.",
            TaskType.REFACTOR.value: "Refactor the relevant code while respecting constraints.",
            TaskType.MIGRATION.value: "Plan or perform the requested migration safely.",
            TaskType.FEATURE_IMPLEMENTATION.value: "Implement the requested feature.",
            TaskType.CODE_EXPLANATION.value: "Explain the relevant code or behavior clearly.",
            TaskType.ARCHITECTURE_DESIGN.value: "Design the requested architecture or approach.",
            TaskType.DOCUMENTATION.value: "Produce the requested documentation or explanation.",
            TaskType.UNKNOWN.value: stripped[:160] if stripped else "Handle the user's request conservatively.",
        }
        return mapping.get(task_type, stripped[:160] if stripped else "Handle the user's request conservatively.")

    def _score_confidence(self, text: str, ambiguities: List[str], high_stakes: bool) -> str:
        if high_stakes or len(ambiguities) >= 3:
            return Confidence.LOW.value
        if len(ambiguities) >= 1:
            return Confidence.MEDIUM.value
        if len(text.split()) < 4:
            return Confidence.MEDIUM.value
        return Confidence.HIGH.value
