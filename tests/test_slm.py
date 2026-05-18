"""Tests for SLM normalizers — uses mocks to avoid real API calls."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prpt.core.types import RepoMetadata, RewriteMode


@pytest.fixture
def repo():
    return RepoMetadata(
        cwd="/tmp/test-repo",
        branch="main",
        changed_files=["src/app.py"],
        dominant_language="Python",
        test_framework="pytest",
    )


# --- Anthropic SLM normalizer ---

class TestSLMNormalizerAnthropic:
    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_rewrite_called(self, mock_anthropic_mod, repo):
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client

        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="Optimized: fix timeout in OrderSyncWorker")],
            usage=SimpleNamespace(input_tokens=50, output_tokens=20),
        )
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            from prpt.normalizers.slm_anthropic import SLMNormalizer
            normalizer = SLMNormalizer.__new__(SLMNormalizer)
            normalizer._client = mock_client
            normalizer._heuristic = __import__(
                "prpt.normalizers.heuristic", fromlist=["HeuristicNormalizer"]
            ).HeuristicNormalizer()
            normalizer._content_loader = None
            normalizer._last_usage = None
            normalizer._last_context_block = None
            normalizer._last_intent = None
            normalizer._last_scope = None

        result = normalizer.normalize("fix timeout in OrderSyncWorker", repo)

        assert result.rewrite_mode == RewriteMode.EXTRACT_PLUS_LIGHT_REWRITE.value
        assert result.original_prompt == "fix timeout in OrderSyncWorker"
        assert "Optimized" in result.normalized_prompt
        # Two calls expected: pass-1 classify + pass-2 rewrite
        assert mock_client.messages.create.call_count == 2

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_fallback_on_api_error(self, mock_anthropic_mod, repo):
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            from prpt.normalizers.slm_anthropic import SLMNormalizer
            normalizer = SLMNormalizer.__new__(SLMNormalizer)
            normalizer._client = mock_client
            normalizer._heuristic = __import__(
                "prpt.normalizers.heuristic", fromlist=["HeuristicNormalizer"]
            ).HeuristicNormalizer()
            normalizer._content_loader = None
            normalizer._last_usage = None
            normalizer._last_context_block = None
            normalizer._last_intent = None
            normalizer._last_scope = None

        result = normalizer.normalize("fix timeout", repo)
        # Falls back to original prompt
        assert result.original_prompt == "fix timeout"

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_token_stats(self, mock_anthropic_mod, repo):
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client

        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="optimized prompt text")],
            usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        )
        mock_client.messages.create.return_value = mock_response
        mock_client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=50)

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            from prpt.normalizers.slm_anthropic import SLMNormalizer
            normalizer = SLMNormalizer.__new__(SLMNormalizer)
            normalizer._client = mock_client
            normalizer._heuristic = __import__(
                "prpt.normalizers.heuristic", fromlist=["HeuristicNormalizer"]
            ).HeuristicNormalizer()
            normalizer._content_loader = None
            normalizer._last_usage = None
            normalizer._last_context_block = None
            normalizer._last_intent = None
            normalizer._last_scope = None
            normalizer.MODEL = "claude-haiku-4-5-20251001"

        normalizer.normalize("fix timeout", repo)
        stats = normalizer.compute_token_stats("fix timeout", "optimized prompt", "claude-opus-4-6")

        assert stats is not None
        assert stats.haiku_input_tokens == 100
        assert stats.haiku_output_tokens == 30
        assert stats.target_model == "claude-opus-4-6"


# --- OpenAI SLM normalizer ---

class TestSLMNormalizerOpenAI:
    @patch("prpt.normalizers.slm_openai.openai", create=True)
    @patch("prpt.normalizers.slm_openai.tiktoken", create=True)
    def test_rewrite_called(self, mock_tiktoken, mock_openai_mod, repo):
        mock_client = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Optimized OpenAI prompt"))],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=15),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai_mod, "tiktoken": mock_tiktoken}):
            from prpt.normalizers.slm_openai import OpenAISLMNormalizer
            normalizer = OpenAISLMNormalizer.__new__(OpenAISLMNormalizer)
            normalizer._client = mock_client
            normalizer._heuristic = __import__(
                "prpt.normalizers.heuristic", fromlist=["HeuristicNormalizer"]
            ).HeuristicNormalizer()
            normalizer._content_loader = None
            normalizer._last_usage = None
            normalizer._last_context_block = None
            normalizer._last_intent = None
            normalizer._last_scope = None
            normalizer.MODEL = "gpt-4o-mini"
            normalizer.MAX_TOKENS = 512

        result = normalizer.normalize("add dark mode to settings", repo)

        assert result.rewrite_mode == RewriteMode.EXTRACT_PLUS_LIGHT_REWRITE.value
        assert "Optimized OpenAI" in result.normalized_prompt
        # Two calls expected: pass-1 classify + pass-2 rewrite
        assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# _parse_intent_response — scope parsing
# ---------------------------------------------------------------------------

class TestParseIntentResponse:
    def _parse(self, text):
        from prpt.normalizers.slm_anthropic import SLMNormalizer
        return SLMNormalizer._parse_intent_response(text)

    def test_full_header_act_pinpoint(self):
        raw = "INTENT: act\nSCOPE: pinpoint\n---\nFix the null check in send()"
        intent, scope, rewritten = self._parse(raw)
        assert intent == "act"
        assert scope == "pinpoint"
        assert rewritten == "Fix the null check in send()"

    def test_full_header_explain_new(self):
        raw = "INTENT: explain\nSCOPE: new\n---\nHow does the retry logic work?"
        intent, scope, rewritten = self._parse(raw)
        assert intent == "explain"
        assert scope == "new"
        assert rewritten == "How does the retry logic work?"

    def test_localized_scope(self):
        raw = "INTENT: act\nSCOPE: localized\n---\nRefactor the auth middleware"
        intent, scope, _ = self._parse(raw)
        assert scope == "localized"

    def test_broad_scope(self):
        raw = "INTENT: act\nSCOPE: broad\n---\nMigrate all sync calls to async"
        intent, scope, _ = self._parse(raw)
        assert scope == "broad"

    def test_missing_scope_defaults_to_localized(self):
        raw = "INTENT: act\n---\nFix the bug"
        intent, scope, rewritten = self._parse(raw)
        assert intent == "act"
        assert scope == "localized"  # safe default
        assert rewritten == "Fix the bug"

    def test_no_header_falls_back_fully(self):
        raw = "Just the rewritten prompt with no header"
        intent, scope, rewritten = self._parse(raw)
        assert intent == "act"
        assert scope == "localized"
        assert "rewritten prompt" in rewritten

    def test_invalid_intent_value_defaults_to_act(self):
        raw = "INTENT: unknown\nSCOPE: pinpoint\n---\nDo something"
        intent, scope, _ = self._parse(raw)
        assert intent == "act"

    def test_invalid_scope_value_defaults_to_localized(self):
        raw = "INTENT: act\nSCOPE: mega\n---\nDo something"
        _, scope, _ = self._parse(raw)
        assert scope == "localized"

    def test_multiline_rewritten_prompt_preserved(self):
        raw = "INTENT: act\nSCOPE: pinpoint\n---\nLine one\nLine two\nLine three"
        _, _, rewritten = self._parse(raw)
        assert "Line one" in rewritten
        assert "Line three" in rewritten


# ---------------------------------------------------------------------------
# build_output_suffix
# ---------------------------------------------------------------------------

class TestBuildOutputSuffix:
    def _suffix(self, scope, tool):
        from prpt.normalizers.base import build_output_suffix
        return build_output_suffix(scope, tool)

    def test_pinpoint_claude_code(self):
        s = self._suffix("pinpoint", "anthropic")
        assert "Edit tool" in s
        assert "Write" in s

    def test_pinpoint_codex(self):
        s = self._suffix("pinpoint", "codex")
        assert "No preamble" in s or "no" in s.lower()
        assert s  # non-empty

    def test_pinpoint_generic_tool(self):
        s = self._suffix("pinpoint", "some-other-tool")
        assert s  # non-empty

    def test_localized_anthropic(self):
        s = self._suffix("localized", "anthropic")
        assert s
        assert "Edit" in s

    def test_broad_returns_empty(self):
        s = self._suffix("broad", "anthropic")
        assert s == ""

    def test_new_returns_empty(self):
        s = self._suffix("new", "codex")
        assert s == ""

    def test_none_scope_returns_empty(self):
        s = self._suffix(None, "anthropic")
        assert s == ""

    def test_claude_alias(self):
        s1 = self._suffix("pinpoint", "claude")
        s2 = self._suffix("pinpoint", "anthropic")
        assert s1 == s2

    def test_claude_code_alias(self):
        s1 = self._suffix("pinpoint", "claude-code")
        s2 = self._suffix("pinpoint", "anthropic")
        assert s1 == s2


# ---------------------------------------------------------------------------
# Anthropic prompt caching
# ---------------------------------------------------------------------------

def _make_normalizer_with_mock(mock_client, context_block: str | None = None):
    """Build an SLMNormalizer with injected mock client + optional cached context."""
    from prpt.normalizers.slm_anthropic import SLMNormalizer
    normalizer = SLMNormalizer.__new__(SLMNormalizer)
    normalizer._client = mock_client
    normalizer._heuristic = __import__(
        "prpt.normalizers.heuristic", fromlist=["HeuristicNormalizer"]
    ).HeuristicNormalizer()
    normalizer._content_loader = None
    normalizer._last_usage = None
    normalizer._last_context_block = context_block
    normalizer._last_intent = None
    normalizer._last_scope = None
    normalizer.MODEL = "claude-haiku-4-5-20251001"
    return normalizer


class TestAnthropicPromptCaching:
    """Verify cache_control markers are emitted and usage metrics tracked."""

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_cache_control_present_on_large_context(self, mock_anthropic_mod, repo):
        """A ≥8KB context block should get cache_control: ephemeral."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client

        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(
                input_tokens=50, output_tokens=20,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=2500,
            ),
        )
        mock_client.messages.create.return_value = mock_response

        # Large pre-loaded context (>= _CACHE_MIN_CHARS = 8192)
        big_context = "X" * 10_000

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block=big_context)
            normalizer.normalize("fix timeout", repo)

        # Verify the call args — system is a block-list, user content has cache markers
        call = mock_client.messages.create.call_args
        system_arg = call.kwargs.get("system")
        assert isinstance(system_arg, list)
        assert system_arg[0].get("cache_control") == {"type": "ephemeral"}

        messages = call.kwargs.get("messages")
        content_blocks = messages[0]["content"]
        assert isinstance(content_blocks, list)
        # First block is the repo context → must be cached
        assert content_blocks[0].get("cache_control") == {"type": "ephemeral"}
        # Second block is the dynamic user prompt → must NOT be cached
        assert "cache_control" not in content_blocks[1]

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_cache_control_absent_on_small_context(self, mock_anthropic_mod, repo):
        """Short context (< 8KB) should not trigger cache_control markers."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(input_tokens=30, output_tokens=10),
        )
        mock_client.messages.create.return_value = mock_response

        tiny_context = "small context"

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block=tiny_context)
            normalizer.normalize("fix timeout", repo)

        call = mock_client.messages.create.call_args
        system_arg = call.kwargs.get("system")
        # System should still be a block list, but without cache_control
        assert isinstance(system_arg, list)
        assert "cache_control" not in system_arg[0]

        content_blocks = call.kwargs.get("messages")[0]["content"]
        # First block is repo context, no cache_control since it's tiny
        assert "cache_control" not in content_blocks[0]

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_cache_read_tokens_tracked(self, mock_anthropic_mod, repo):
        """Response with cache_read_input_tokens must flow into _last_usage."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(
                input_tokens=20, output_tokens=15,
                cache_read_input_tokens=3000,
                cache_creation_input_tokens=0,
            ),
        )
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block="X" * 10_000)
            normalizer.normalize("fix timeout", repo)

        assert normalizer._last_usage["cache_read_input_tokens"] == 3000
        assert normalizer._last_usage["cache_creation_input_tokens"] == 0

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_cache_write_tokens_tracked(self, mock_anthropic_mod, repo):
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(
                input_tokens=50, output_tokens=20,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=2500,
            ),
        )
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block="X" * 10_000)
            normalizer.normalize("fix timeout", repo)

        assert normalizer._last_usage["cache_creation_input_tokens"] == 2500

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_cache_savings_computed(self, mock_anthropic_mod, repo):
        """Cache hit should yield positive cache_savings_usd."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten prompt")],
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=20,
                cache_read_input_tokens=10_000,
                cache_creation_input_tokens=0,
            ),
        )
        mock_client.messages.create.return_value = mock_response
        mock_client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=50)

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block="X" * 10_000)
            normalizer.normalize("fix timeout", repo)
            stats = normalizer.compute_token_stats(
                "fix timeout", "rewritten prompt", "claude-opus-4-6"
            )

        assert stats is not None
        assert stats.cache_read_input_tokens == 10_000
        assert stats.cache_creation_input_tokens == 0
        assert stats.cache_savings_usd > 0  # saved ~0.9x * input price on 10K tokens

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_backward_compat_no_cache_fields(self, mock_anthropic_mod, repo):
        """A response without cache_* fields must still work (cache counts = 0)."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(input_tokens=50, output_tokens=20),  # no cache fields
        )
        mock_client.messages.create.return_value = mock_response
        mock_client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=50)

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block="X" * 10_000)
            normalizer.normalize("fix timeout", repo)
            stats = normalizer.compute_token_stats("fix", "rewritten", "claude-opus-4-6")

        assert stats.cache_read_input_tokens == 0
        assert stats.cache_creation_input_tokens == 0
        assert stats.cache_savings_usd == 0.0

    @patch("prpt.normalizers.slm_anthropic.anthropic", create=True)
    def test_no_context_no_cache_markers(self, mock_anthropic_mod, repo):
        """When there's no context block at all, no cache markers anywhere."""
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_response = SimpleNamespace(
            content=[SimpleNamespace(text="INTENT: act\nSCOPE: pinpoint\n---\nrewritten")],
            usage=SimpleNamespace(input_tokens=20, output_tokens=10),
        )
        mock_client.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            normalizer = _make_normalizer_with_mock(mock_client, context_block="")
            normalizer.normalize("fix timeout", repo)

        call = mock_client.messages.create.call_args
        system_arg = call.kwargs.get("system")
        assert isinstance(system_arg, list)
        assert "cache_control" not in system_arg[0]

        # User content is just a string (no split needed) or single block without cache
        messages = call.kwargs.get("messages")
        content = messages[0]["content"]
        if isinstance(content, list):
            for block in content:
                assert "cache_control" not in block

    def test_system_blocks_helper(self):
        from prpt.normalizers.slm_anthropic import _system_blocks
        blocks = _system_blocks("hello", enable_cache=True)
        assert blocks == [{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}]

        blocks2 = _system_blocks("hello", enable_cache=False)
        assert blocks2 == [{"type": "text", "text": "hello"}]

    def test_user_blocks_helper(self):
        from prpt.normalizers.slm_anthropic import _user_blocks_with_context
        blocks = _user_blocks_with_context("BIG" * 5000, "prompt", enable_cache=True)
        assert len(blocks) == 2
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1]
        assert "repository_context" in blocks[0]["text"]
        assert "developer_prompt" in blocks[1]["text"]


# --- v2 ExecutionSpec parser + slm-openai-v2 normalizer ---
#
# Parser-migration guardrails (per project_llmwrap_v2_roadmap.md):
#   1. Valid JSON spec -> NormalizedRequest + _last_intent + _last_scope populated
#   2. Equivalent prose envelope -> identical NormalizedRequest shape for same input
#   3. Malformed JSON -> fail open (defaults), never crash, never emit raw broken JSON

class TestExecutionSpecParser:
    def test_valid_json_spec_parses_with_all_fields(self):
        from prpt.core.spec import parse_spec_json
        raw = (
            '{"route": "act", "intent": "act", "scope": "pinpoint", '
            '"needs_history": false, "context_policy": "targeted", '
            '"target_files": ["httpx/_client.py"], "risk": "low", '
            '"downstream_prompt": "Fix the timeout bug in BaseClient._set_timeout.", '
            '"memory_record": "User wants timeout passthrough fix in sync client."}'
        )
        spec = parse_spec_json(raw)
        assert spec is not None
        assert spec.route == "act"
        assert spec.intent == "act"
        assert spec.scope == "pinpoint"
        assert spec.needs_history is False
        assert spec.context_policy == "targeted"
        assert spec.target_files == ["httpx/_client.py"]
        assert spec.risk == "low"
        assert spec.downstream_prompt.startswith("Fix the timeout")
        assert "timeout passthrough" in spec.memory_record

    def test_valid_json_with_fenced_block_parses(self):
        from prpt.core.spec import parse_spec_json
        raw = (
            "```json\n"
            '{"intent": "act", "scope": "localized", '
            '"downstream_prompt": "Refactor auth.py"}\n'
            "```"
        )
        spec = parse_spec_json(raw)
        assert spec is not None
        assert spec.downstream_prompt == "Refactor auth.py"
        assert spec.intent == "act"
        # Missing fields get defaults
        assert spec.route == "act"
        assert spec.risk == "low"

    def test_malformed_json_returns_none(self):
        from prpt.core.spec import parse_spec_json
        # Caller falls back to prose parser on None
        assert parse_spec_json("INTENT: act\nSCOPE: localized\n---\nrewrite") is None
        assert parse_spec_json("not json at all") is None
        assert parse_spec_json("") is None
        assert parse_spec_json('{"intent": "act"}') is None  # missing downstream_prompt
        assert parse_spec_json('{"downstream_prompt": 42}') is None  # wrong type

    def test_invalid_enum_values_clamped_to_defaults(self):
        from prpt.core.spec import parse_spec_json
        raw = (
            '{"route": "INVALID", "intent": "WRONG", "scope": "??", '
            '"context_policy": "??", "risk": "??", '
            '"downstream_prompt": "do the thing"}'
        )
        spec = parse_spec_json(raw)
        assert spec is not None
        assert spec.route == "act"  # default
        assert spec.intent == "act"  # default
        assert spec.scope == "localized"  # default
        assert spec.context_policy == "targeted"  # default
        assert spec.risk == "low"  # default
        assert spec.downstream_prompt == "do the thing"


class TestSLMOpenAIV2Normalizer:
    """Three regression tests per parser-migration guardrails."""

    def _make_normalizer(self, mock_client):
        """Build an OpenAISLMNormalizerV2 with a mock OpenAI client."""
        from prpt.normalizers.heuristic import HeuristicNormalizer
        from prpt.normalizers.slm_openai_v2 import OpenAISLMNormalizerV2
        n = OpenAISLMNormalizerV2.__new__(OpenAISLMNormalizerV2)
        n._client = mock_client
        n._heuristic = HeuristicNormalizer()
        n._content_loader = None
        n._last_usage = None
        n._last_context_block = ""  # short-circuit context loading
        n._last_intent = None
        n._last_scope = None
        n._last_spec = None
        return n

    def _mock_response(self, content: str):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=30),
        )

    def test_1_valid_json_spec_populates_required_fields(self, repo):
        """Per guardrail #1: valid JSON spec must populate NormalizedRequest +
        _last_intent + _last_scope so build_output_suffix and history-gating work."""
        client = MagicMock()
        json_body = (
            '{"route": "act", "intent": "act", "scope": "pinpoint", '
            '"needs_history": false, "context_policy": "targeted", '
            '"target_files": ["httpx/_client.py"], "risk": "low", '
            '"downstream_prompt": "Fix the timeout in BaseClient.", '
            '"memory_record": "User wants timeout fix."}'
        )
        client.chat.completions.create.return_value = self._mock_response(json_body)

        n = self._make_normalizer(client)
        result = n.normalize("fix the timeout", repo)

        # Stable NormalizedRequest contract
        assert result.normalized_prompt == "Fix the timeout in BaseClient."
        assert result.original_prompt == "fix the timeout"
        # _last_intent + _last_scope populated for build_output_suffix
        assert n._last_intent == "act"
        assert n._last_scope == "pinpoint"
        # Full spec stashed for callers that want it
        assert n._last_spec is not None
        assert n._last_spec.target_files == ["httpx/_client.py"]
        assert n._last_spec.memory_record == "User wants timeout fix."

    def test_2_prose_envelope_fallback_produces_equivalent_shape(self, repo):
        """Per guardrail #2: when SLM returns the v1 prose envelope instead of
        JSON, the parser falls back and produces the same NormalizedRequest
        shape so chain harness A/B comparisons aren't poisoned by parser bugs."""
        client = MagicMock()
        prose = "INTENT: act\nSCOPE: pinpoint\n---\nFix the timeout in BaseClient."
        client.chat.completions.create.return_value = self._mock_response(prose)

        n = self._make_normalizer(client)
        result = n.normalize("fix the timeout", repo)

        # Same NormalizedRequest shape as the JSON path
        assert result.normalized_prompt == "Fix the timeout in BaseClient."
        assert result.original_prompt == "fix the timeout"
        # _last_intent + _last_scope populated by prose fallback
        assert n._last_intent == "act"
        assert n._last_scope == "pinpoint"
        # No spec available on fallback path
        assert n._last_spec is None

    def test_3_malformed_json_fails_open_never_crashes(self, repo):
        """Per guardrail #3: malformed JSON must fail open. The downstream
        prompt should NOT be the raw broken JSON blob (which would slm_anthropic
        line-302's `('act','localized',raw_text)` fallback produce). Heuristic
        defaults apply, request still serviceable."""
        client = MagicMock()
        # Truly broken: not JSON, no INTENT/SCOPE/--- envelope either
        client.chat.completions.create.return_value = self._mock_response(
            "{this is not valid: json and also no prose envelope"
        )

        n = self._make_normalizer(client)
        # Must not raise
        result = n.normalize("fix the timeout", repo)

        # Must NOT have sent the raw broken blob downstream as the rewrite
        assert "{this is not valid" not in result.normalized_prompt or \
            result.normalized_prompt == "fix the timeout"
        # _last_intent + _last_scope have safe defaults from prose parser
        assert n._last_intent in ("act", "explain")
        assert n._last_scope in ("pinpoint", "localized", "broad", "new")


class TestTargetFilesHint:
    """v2 roadmap #5 benchmark hook: build_final_downstream_prompt consumes
    spec.target_files and appends `[likely files: ...]` when the
    PROMPTPILOT_USE_TARGET_HINT env var is set. Off by default so all prior
    callers see zero behavior change."""

    @staticmethod
    def _make_normalized(rewrite: str):
        from prpt.core.types import NormalizedRequest, RewriteMode
        return NormalizedRequest(
            original_prompt=rewrite, task_type="bugfix", objective=rewrite,
            explicit_context=[], hard_constraints=[], soft_preferences=[],
            requested_output=[], protected_spans=[], ambiguities=[],
            assumptions=[], omissions=[], confidence="medium", needs_review=False,
            rewrite_mode=RewriteMode.EXTRACT_PLUS_LIGHT_REWRITE.value,
            normalized_prompt=rewrite,
        )

    @staticmethod
    def _repo():
        from prpt.core.types import RepoMetadata
        return RepoMetadata(cwd="C:/projects/httpx", branch="master",
                            dominant_language="python", test_framework="pytest")

    def test_helper_empty_when_env_var_unset(self, monkeypatch):
        from prpt.normalizers.base import _format_target_files_hint
        monkeypatch.delenv("PROMPTPILOT_USE_TARGET_HINT", raising=False)
        assert _format_target_files_hint(["a.py", "b.py"]) == ""

    def test_helper_empty_when_env_var_set_but_files_empty(self, monkeypatch):
        from prpt.normalizers.base import _format_target_files_hint
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        assert _format_target_files_hint([]) == ""
        assert _format_target_files_hint(None) == ""

    def test_helper_emits_hint_when_enabled(self, monkeypatch):
        from prpt.normalizers.base import _format_target_files_hint
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        assert _format_target_files_hint(["a.py", "b.py"]) == "\n[likely files: a.py, b.py]"

    def test_helper_skips_blank_entries(self, monkeypatch):
        from prpt.normalizers.base import _format_target_files_hint
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        out = _format_target_files_hint(["a.py", "", "  ", "b.py", None])
        assert out == "\n[likely files: a.py, b.py]"

    def test_helper_caps_at_eight_with_overflow_marker(self, monkeypatch):
        from prpt.normalizers.base import _format_target_files_hint
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        files = ["f{0}.py".format(i) for i in range(10)]
        out = _format_target_files_hint(files)
        assert "f0.py" in out and "f7.py" in out
        assert "f8.py" not in out and "f9.py" not in out
        assert "(+2 more)" in out

    def test_helper_only_triggers_on_literal_1(self, monkeypatch):
        """Defensive: only "1" enables; "true"/"yes"/etc. do not, mirroring
        the existing kill-switch convention (PROMPTPILOT_COMPRESS_DISABLE=1)."""
        from prpt.normalizers.base import _format_target_files_hint
        for v in ("true", "yes", "True", "0", ""):
            monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", v)
            assert _format_target_files_hint(["a.py"]) == "", "expected off for env={0!r}".format(v)

    def test_build_final_with_target_files_off_matches_prior_behavior(self, monkeypatch):
        """Backward compat: default-off + no target_files arg = byte-identical
        to the pre-#5 output. This is the v1 callers' guarantee."""
        from prpt.normalizers.base import build_final_downstream_prompt
        monkeypatch.delenv("PROMPTPILOT_USE_TARGET_HINT", raising=False)
        out = build_final_downstream_prompt(self._make_normalized("Fix the timeout."), self._repo())
        assert out.endswith("[cwd=C:/projects/httpx; python; branch=master; tests=pytest]")
        assert "[likely files:" not in out

    def test_build_final_with_target_files_on_appends_hint(self, monkeypatch):
        from prpt.normalizers.base import build_final_downstream_prompt
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        out = build_final_downstream_prompt(
            self._make_normalized("Fix the timeout."),
            self._repo(),
            target_files=["httpx/_client.py", "httpx/_config.py"],
        )
        assert out.endswith("\n[likely files: httpx/_client.py, httpx/_config.py]")
        # The cwd line still precedes the hint
        assert "[cwd=C:/projects/httpx" in out

    def test_build_final_with_env_on_but_empty_target_files_omits_hint(self, monkeypatch):
        """Env on, but the SLM didn't predict any files — don't emit an empty
        hint that would look like the SLM said 'no files needed.'"""
        from prpt.normalizers.base import build_final_downstream_prompt
        monkeypatch.setenv("PROMPTPILOT_USE_TARGET_HINT", "1")
        out = build_final_downstream_prompt(
            self._make_normalized("Explain X."), self._repo(), target_files=[],
        )
        assert "[likely files:" not in out
