"""Tests for the heuristic normalizer."""
from __future__ import annotations

import pytest

from prpt.core.types import RepoMetadata, RewriteMode, TaskType, Confidence
from prpt.normalizers.heuristic import HeuristicNormalizer


@pytest.fixture
def normalizer():
    return HeuristicNormalizer()


@pytest.fixture
def repo():
    return RepoMetadata(
        cwd="/tmp/test-repo",
        branch="main",
        changed_files=["src/app.py", "tests/test_app.py"],
        dominant_language="Python",
        test_framework="pytest",
    )


@pytest.fixture
def empty_repo():
    return RepoMetadata(cwd="/tmp/empty-repo")


# --- Task type detection ---

class TestTaskTypeDetection:
    def test_bug_fix(self, normalizer, repo):
        result = normalizer.normalize("fix the timeout in OrderSyncWorker", repo)
        assert result.task_type == TaskType.BUG_FIX.value

    def test_refactor(self, normalizer, repo):
        result = normalizer.normalize("refactor the auth module to use dependency injection", repo)
        assert result.task_type == TaskType.REFACTOR.value

    def test_test_generation(self, normalizer, repo):
        result = normalizer.normalize("generate tests for the payment handler", repo)
        assert result.task_type == TaskType.TEST_GENERATION.value

    def test_root_cause_analysis(self, normalizer, repo):
        result = normalizer.normalize("why is the API returning 500 errors?", repo)
        assert result.task_type == TaskType.ROOT_CAUSE_ANALYSIS.value

    def test_feature_implementation(self, normalizer, repo):
        result = normalizer.normalize("implement dark mode toggle in settings page", repo)
        assert result.task_type == TaskType.FEATURE_IMPLEMENTATION.value

    def test_migration(self, normalizer, repo):
        result = normalizer.normalize("migrate the database from postgres 14 to 16", repo)
        assert result.task_type == TaskType.MIGRATION.value

    def test_performance(self, normalizer, repo):
        result = normalizer.normalize(
            "optimize the slow checkout endpoint, reduce p95 latency", repo)
        assert result.task_type == TaskType.PERFORMANCE.value

    def test_unknown(self, normalizer, repo):
        result = normalizer.normalize("do the thing with the stuff", repo)
        assert result.task_type == TaskType.UNKNOWN.value


# --- Constraint extraction ---

class TestConstraintExtraction:
    def test_hard_constraints_detected(self, normalizer, repo):
        result = normalizer.normalize(
            "fix the bug, must not change the public API, avoid downtime", repo
        )
        assert len(result.hard_constraints) > 0
        found = " ".join(result.hard_constraints).lower()
        assert "must not" in found or "avoid" in found

    def test_protected_spans(self, normalizer, repo):
        result = normalizer.normalize(
            "refactor auth but do not touch `legacy_handler.py`, minimal patch only", repo
        )
        assert any("legacy_handler.py" in s for s in result.protected_spans)
        assert any("minimal patch" in s.lower() for s in result.protected_spans)

    def test_requested_output(self, normalizer, repo):
        result = normalizer.normalize(
            "explain root cause and provide a patch with tests", repo
        )
        assert len(result.requested_output) > 0


# --- Confidence and review ---

class TestConfidenceScoring:
    def test_high_confidence_clear_prompt(self, normalizer, repo):
        result = normalizer.normalize(
            "fix the timeout bug in the payment service backend", repo
        )
        assert result.confidence in {Confidence.HIGH.value, Confidence.MEDIUM.value}

    def test_repo_backed_domain_target_is_not_marked_ambiguous(self, normalizer):
        repo = RepoMetadata(
            cwd="/tmp/test-repo",
            changed_files=["payments/processor.py", "tests/test_payments.py"],
            dominant_language="Python",
            test_framework="pytest",
        )
        result = normalizer.normalize("fix the timeout bug in payments", repo)
        assert "Target subsystem is not clearly specified." not in result.ambiguities

    def test_plural_prompt_matches_singular_repo_token(self, normalizer):
        repo = RepoMetadata(
            cwd="/tmp/test-repo",
            changed_files=["payment/handler.py"],
            dominant_language="Python",
            test_framework="pytest",
        )
        result = normalizer.normalize("fix the timeout bug in payments", repo)
        assert "Target subsystem is not clearly specified." not in result.ambiguities

    def test_low_confidence_high_stakes(self, normalizer, repo):
        result = normalizer.normalize(
            "fix the timeout bug in payments", repo, high_stakes=True
        )
        assert result.needs_review is True

    def test_ambiguities_trigger_review(self, normalizer, empty_repo):
        result = normalizer.normalize("fix it", empty_repo)
        assert len(result.ambiguities) >= 1
        assert result.needs_review is True


# --- Output structure ---

class TestOutputStructure:
    def test_rewrite_mode_is_extract_only(self, normalizer, repo):
        result = normalizer.normalize("add dark mode", repo)
        assert result.rewrite_mode == RewriteMode.EXTRACT_ONLY.value

    def test_original_prompt_preserved(self, normalizer, repo):
        prompt = "fix the flaky test in payments module"
        result = normalizer.normalize(prompt, repo)
        assert result.original_prompt == prompt

    def test_normalized_prompt_contains_original(self, normalizer, repo):
        prompt = "fix the flaky test in payments module"
        result = normalizer.normalize(prompt, repo)
        assert prompt in result.normalized_prompt

    def test_all_fields_populated(self, normalizer, repo):
        result = normalizer.normalize("fix the timeout in OrderSyncWorker", repo)
        assert result.task_type
        assert result.objective
        assert isinstance(result.explicit_context, list)
        assert isinstance(result.hard_constraints, list)
        assert isinstance(result.soft_preferences, list)
        assert isinstance(result.requested_output, list)
        assert isinstance(result.protected_spans, list)
        assert isinstance(result.ambiguities, list)
        assert isinstance(result.assumptions, list)
        assert result.confidence in {"high", "medium", "low"}
