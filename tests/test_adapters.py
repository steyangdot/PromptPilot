"""Tests for Anthropic and OpenAI direct adapters (mocked + live)."""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_args(**overrides):
    defaults = dict(verbose=False, cwd="/tmp/test")
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_anthropic_adapter(model="claude-opus-4-7", max_tokens=4096,
                             system=None, context_block=None):
    """Build an AnthropicDirectAdapter bypassing the real SDK import."""
    from promptpilot.adapters.anthropic_adapter import AnthropicDirectAdapter
    adapter = AnthropicDirectAdapter.__new__(AnthropicDirectAdapter)
    adapter._client = MagicMock()
    adapter._model = model
    adapter._max_tokens = max_tokens
    adapter._system = system
    adapter._context_block = context_block
    adapter.last_usage = None
    return adapter


def _fake_usage(input_tokens=100, output_tokens=50,
                cache_creation=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def _fake_response(text="ok", **usage_kw):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=_fake_usage(**usage_kw),
    )


# ---------------------------------------------------------------------------
# Anthropic adapter — mocked (updated for cache-aware last_usage)
# ---------------------------------------------------------------------------

class TestAnthropicDirectAdapterMocked:
    def test_run_returns_text(self, capsys):
        adapter = _make_anthropic_adapter(model="claude-opus-4-7")
        adapter._client.messages.create.return_value = _fake_response(
            text="Here is the fix for the timeout bug.",
            input_tokens=150, output_tokens=80,
        )

        exit_code = adapter.run("fix the timeout bug", _make_args())

        assert exit_code == 0
        # last_usage now includes cache fields + total_cost_usd
        assert adapter.last_usage["input_tokens"] == 150
        assert adapter.last_usage["output_tokens"] == 80
        assert "total_cost_usd" in adapter.last_usage
        output = capsys.readouterr().out
        assert "fix for the timeout bug" in output
        adapter._client.messages.create.assert_called_once()

    def test_run_captures_usage(self):
        adapter = _make_anthropic_adapter(model="claude-sonnet-4-7")
        adapter._client.messages.create.return_value = _fake_response(
            text="done", input_tokens=500, output_tokens=200,
        )

        adapter.run("add dark mode", _make_args())

        assert adapter.last_usage["input_tokens"] == 500
        assert adapter.last_usage["output_tokens"] == 200

    def test_api_error_returns_1(self):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.side_effect = RuntimeError("API overloaded")

        exit_code = adapter.run("fix bug", _make_args())

        assert exit_code == 1
        assert adapter.last_usage is None

    def test_verbose_logs_to_stderr(self, capsys):
        adapter = _make_anthropic_adapter(model="claude-opus-4-7")
        adapter._client.messages.create.return_value = _fake_response(text="ok")

        adapter.run("test", _make_args(verbose=True))

        stderr = capsys.readouterr().err
        assert "claude-opus-4-7" in stderr

    def test_no_system_no_system_kwarg(self):
        """When no system is provided, create() must NOT receive a `system` key."""
        adapter = _make_anthropic_adapter(system=None)
        adapter._client.messages.create.return_value = _fake_response()

        adapter.run("hello", _make_args())

        call_kwargs = adapter._client.messages.create.call_args[1]
        assert "system" not in call_kwargs

    def test_with_system_passes_system_kwarg(self):
        """When system is set, create() must receive a `system` key."""
        adapter = _make_anthropic_adapter(system="You are a coding assistant.")
        adapter._client.messages.create.return_value = _fake_response()

        adapter.run("hello", _make_args())

        call_kwargs = adapter._client.messages.create.call_args[1]
        assert "system" in call_kwargs
        assert call_kwargs["system"][0]["type"] == "text"
        assert "You are a coding assistant." in call_kwargs["system"][0]["text"]

    def test_user_content_plain_string_when_no_context_block(self):
        """Without context_block, user content must be a plain string."""
        adapter = _make_anthropic_adapter(context_block=None)
        adapter._client.messages.create.return_value = _fake_response()

        adapter.run("my task", _make_args())

        call_kwargs = adapter._client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
        assert isinstance(user_content, str)
        assert user_content == "my task"

    def test_user_content_split_when_context_block_provided(self):
        """With context_block, user content must be a list of two blocks."""
        adapter = _make_anthropic_adapter(context_block="repo context here")
        adapter._client.messages.create.return_value = _fake_response()

        adapter.run("my task", _make_args())

        call_kwargs = adapter._client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
        assert isinstance(user_content, list)
        assert len(user_content) == 2
        # First block = wrapped context
        assert "<repository_context>" in user_content[0]["text"]
        assert "repo context here" in user_content[0]["text"]
        # Second block = task prompt
        assert user_content[1]["text"] == "my task"


# ---------------------------------------------------------------------------
# AnthropicDirectAdapter — _build_system / _build_user_content
# ---------------------------------------------------------------------------

class TestAnthropicAdapterCacheBuilders:
    _LARGE = "x" * 5000   # > _CACHE_MIN_CHARS (4096)
    _SMALL = "y" * 100    # < _CACHE_MIN_CHARS

    def test_build_system_none_returns_none(self):
        adapter = _make_anthropic_adapter(system=None)
        assert adapter._build_system() is None

    def test_build_system_small_no_cache_marker(self):
        adapter = _make_anthropic_adapter(system=self._SMALL)
        result = adapter._build_system()
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "cache_control" not in result[0]

    def test_build_system_large_has_cache_marker(self):
        adapter = _make_anthropic_adapter(system=self._LARGE)
        result = adapter._build_system()
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_build_user_content_no_context_is_string(self):
        adapter = _make_anthropic_adapter(context_block=None)
        result = adapter._build_user_content("do the thing")
        assert result == "do the thing"

    def test_build_user_content_small_context_no_cache_marker(self):
        adapter = _make_anthropic_adapter(context_block=self._SMALL)
        result = adapter._build_user_content("task")
        assert isinstance(result, list)
        assert "cache_control" not in result[0]

    def test_build_user_content_large_context_has_cache_marker(self):
        adapter = _make_anthropic_adapter(context_block=self._LARGE)
        result = adapter._build_user_content("task")
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_build_user_content_wraps_context_in_xml_tags(self):
        adapter = _make_anthropic_adapter(context_block="some context")
        result = adapter._build_user_content("my task")
        assert result[0]["text"].startswith("<repository_context>")
        assert result[0]["text"].endswith("</repository_context>")
        assert "some context" in result[0]["text"]

    def test_build_user_content_task_prompt_is_last_block(self):
        adapter = _make_anthropic_adapter(context_block="ctx")
        result = adapter._build_user_content("run tests")
        assert result[-1]["text"] == "run tests"


# ---------------------------------------------------------------------------
# AnthropicDirectAdapter — cache-aware usage tracking
# ---------------------------------------------------------------------------

class TestAnthropicAdapterCacheUsage:
    def test_cache_tokens_captured_in_last_usage(self):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = _fake_response(
            input_tokens=1000, output_tokens=200,
            cache_creation=500, cache_read=300,
        )

        adapter.run("task", _make_args())

        assert adapter.last_usage["cache_creation_input_tokens"] == 500
        assert adapter.last_usage["cache_read_input_tokens"] == 300

    def test_total_cost_usd_present_in_last_usage(self):
        adapter = _make_anthropic_adapter(model="claude-opus-4-7")
        adapter._client.messages.create.return_value = _fake_response(
            input_tokens=1_000_000, output_tokens=0,
        )

        adapter.run("task", _make_args())

        # Opus 4.7: $15/M input → 1M tokens = $15.00
        assert abs(adapter.last_usage["total_cost_usd"] - 15.0) < 0.01

    def test_cache_read_costs_less(self):
        """Cache read should be cheaper than normal input at 0.10× rate."""
        from promptpilot.adapters.anthropic_adapter import _cost_usd

        # 1M normal input vs 1M cache-read — cache should be 10× cheaper
        normal = _cost_usd(
            {"input_tokens": 1_000_000, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "claude-opus-4-7",
        )
        cached = _cost_usd(
            {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1_000_000},
            "claude-opus-4-7",
        )
        assert cached == pytest.approx(normal * 0.10, rel=1e-6)

    def test_cache_write_costs_more(self):
        """Cache write is billed at 1.25× normal input."""
        from promptpilot.adapters.anthropic_adapter import _cost_usd

        normal = _cost_usd(
            {"input_tokens": 1_000_000, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "claude-sonnet-4-7",
        )
        written = _cost_usd(
            {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 0},
            "claude-sonnet-4-7",
        )
        assert written == pytest.approx(normal * 1.25, rel=1e-6)

    def test_zero_cache_tokens_when_missing_from_usage(self):
        """Usage objects without cache fields default to 0 — no crash."""
        adapter = _make_anthropic_adapter()
        # Usage has NO cache fields at all
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )

        adapter.run("task", _make_args())

        assert adapter.last_usage["cache_creation_input_tokens"] == 0
        assert adapter.last_usage["cache_read_input_tokens"] == 0


# ---------------------------------------------------------------------------
# AnthropicDirectAdapter — cache_summary()
# ---------------------------------------------------------------------------

class TestAnthropicAdapterCacheSummary:
    def test_no_run_yet(self):
        adapter = _make_anthropic_adapter()
        assert adapter.cache_summary() == "no run yet"

    def test_no_cache_tokens(self):
        adapter = _make_anthropic_adapter()
        adapter.last_usage = {
            "input_tokens": 500, "output_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost_usd": 0.0075,
        }
        summary = adapter.cache_summary()
        assert "off" in summary

    def test_cache_hit_summary(self):
        adapter = _make_anthropic_adapter()
        adapter.last_usage = {
            "input_tokens": 200, "output_tokens": 100,
            "cache_creation_input_tokens": 4096,
            "cache_read_input_tokens": 4096,
            "total_cost_usd": 0.005,
        }
        summary = adapter.cache_summary()
        assert "4,096 read" in summary
        assert "4,096 written" in summary
        assert "50%" in summary  # 4096/(4096+4096) = 50%
        assert "$" in summary

    def test_cache_all_writes_zero_pct(self):
        adapter = _make_anthropic_adapter()
        adapter.last_usage = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 8192,
            "cache_read_input_tokens": 0,
            "total_cost_usd": 0.0,
        }
        summary = adapter.cache_summary()
        assert "0% hit" in summary

    def test_cache_all_reads_100_pct(self):
        adapter = _make_anthropic_adapter()
        adapter.last_usage = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8192,
            "total_cost_usd": 0.0,
        }
        summary = adapter.cache_summary()
        assert "100% hit" in summary


# ---------------------------------------------------------------------------
# AnthropicDirectAdapter — Opus 4.7 model integration
# ---------------------------------------------------------------------------

class TestAnthropicAdapterOpus47:
    def test_opus47_model_id_stored(self):
        adapter = _make_anthropic_adapter(model="claude-opus-4-7")
        assert adapter._model == "claude-opus-4-7"

    def test_opus47_pricing_applied(self):
        """Opus 4.7 uses the same $15/$75 pricing as Opus 4.6."""
        from promptpilot.adapters.anthropic_adapter import _cost_usd

        cost = _cost_usd(
            {"input_tokens": 0, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "claude-opus-4-7",
        )
        assert cost == pytest.approx(75.0, rel=1e-6)

    def test_sonnet47_pricing_applied(self):
        from promptpilot.adapters.anthropic_adapter import _cost_usd

        cost = _cost_usd(
            {"input_tokens": 1_000_000, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "claude-sonnet-4-7",
        )
        assert cost == pytest.approx(3.0, rel=1e-6)

    def test_unknown_model_falls_back_to_opus_pricing(self):
        """Unknown models default to $15/$75 (Opus pricing)."""
        from promptpilot.adapters.anthropic_adapter import _cost_usd

        cost = _cost_usd(
            {"input_tokens": 1_000_000, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            "claude-unknown-model",
        )
        assert cost == pytest.approx(15.0, rel=1e-6)

    def test_run_with_opus47_calls_correct_model(self):
        adapter = _make_anthropic_adapter(model="claude-opus-4-7")
        adapter._client.messages.create.return_value = _fake_response()

        adapter.run("task", _make_args())

        call_kwargs = adapter._client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# OpenAI adapter — mocked
# ---------------------------------------------------------------------------

class TestOpenAIDirectAdapterMocked:
    @patch("promptpilot.adapters.openai_adapter.openai", create=True)
    def test_run_returns_text(self, mock_openai_mod, capsys):
        mock_client = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Dark mode implemented."))],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=90),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter
            adapter = OpenAIDirectAdapter.__new__(OpenAIDirectAdapter)
            adapter._client = mock_client
            adapter._model = "gpt-4o"
            adapter._max_tokens = 4096
            adapter.last_usage = None

        exit_code = adapter.run("add dark mode", _make_args())

        assert exit_code == 0
        assert adapter.last_usage == {"input_tokens": 120, "output_tokens": 90}
        output = capsys.readouterr().out
        assert "Dark mode implemented" in output
        mock_client.chat.completions.create.assert_called_once()

    @patch("promptpilot.adapters.openai_adapter.openai", create=True)
    def test_usage_mapping(self, mock_openai_mod):
        """Verify prompt_tokens -> input_tokens and completion_tokens -> output_tokens mapping."""
        mock_client = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done"))],
            usage=SimpleNamespace(prompt_tokens=999, completion_tokens=333),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter
            adapter = OpenAIDirectAdapter.__new__(OpenAIDirectAdapter)
            adapter._client = mock_client
            adapter._model = "gpt-4o"
            adapter._max_tokens = 4096
            adapter.last_usage = None

        adapter.run("test", _make_args())

        assert adapter.last_usage["input_tokens"] == 999
        assert adapter.last_usage["output_tokens"] == 333

    @patch("promptpilot.adapters.openai_adapter.openai", create=True)
    def test_api_error_returns_1(self, mock_openai_mod):
        mock_client = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("rate limited")

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter
            adapter = OpenAIDirectAdapter.__new__(OpenAIDirectAdapter)
            adapter._client = mock_client
            adapter._model = "gpt-4o"
            adapter._max_tokens = 4096
            adapter.last_usage = None

        exit_code = adapter.run("fix bug", _make_args())

        assert exit_code == 1
        assert adapter.last_usage is None

    @patch("promptpilot.adapters.openai_adapter.openai", create=True)
    def test_none_content_handled(self, mock_openai_mod, capsys):
        """If the model returns None content, it should not crash."""
        mock_client = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client

        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=0),
        )
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter
            adapter = OpenAIDirectAdapter.__new__(OpenAIDirectAdapter)
            adapter._client = mock_client
            adapter._model = "gpt-4o"
            adapter._max_tokens = 4096
            adapter.last_usage = None

        exit_code = adapter.run("test", _make_args())

        assert exit_code == 0
        output = capsys.readouterr().out
        assert output == ""  # No content printed


# ---------------------------------------------------------------------------
# Fix #1 — shell.resolve_executable_name prefers .exe over .cmd/.bat
# ---------------------------------------------------------------------------

class TestShellExecutableResolution:
    def test_windows_prefers_exe_over_cmd(self):
        """Prefer .exe (safe) over .cmd/.bat (CVE-2024-3220 class)."""
        from promptpilot.adapters import shell

        with patch.object(shell, "os") as mock_os:
            mock_os.name = "nt"
            calls = []

            def fake_which(name):
                calls.append(name)
                # Simulate both .cmd and .exe existing — .exe must win.
                if name.endswith(".exe"):
                    return "C:\\tools\\tool.exe"
                if name.endswith(".cmd"):
                    return "C:\\tools\\tool.cmd"
                return None

            with patch.object(shell.shutil, "which", side_effect=fake_which):
                result = shell.resolve_executable_name("tool")

            assert result == "C:\\tools\\tool.exe"
            # .exe must be probed before .cmd — if .cmd was probed at all,
            # the resolver would have short-circuited on .exe already.
            assert "tool.exe" in calls
            if "tool.cmd" in calls:
                assert calls.index("tool.exe") < calls.index("tool.cmd")

    def test_falls_back_to_cmd_when_no_exe(self):
        """If only .cmd exists, we still resolve it (best effort)."""
        from promptpilot.adapters import shell

        with patch.object(shell, "os") as mock_os:
            mock_os.name = "nt"

            def fake_which(name):
                return "C:\\tools\\tool.cmd" if name.endswith(".cmd") else None

            with patch.object(shell.shutil, "which", side_effect=fake_which):
                result = shell.resolve_executable_name("tool")

            assert result == "C:\\tools\\tool.cmd"


# ---------------------------------------------------------------------------
# Fix #3 — OpenAI o-series reasoning models use max_completion_tokens
# ---------------------------------------------------------------------------

class TestOpenAIReasoningModelKwarg:
    @pytest.mark.parametrize("model,expected_kwarg", [
        ("gpt-4o", "max_tokens"),
        ("gpt-4o-mini", "max_tokens"),
        ("gpt-5", "max_tokens"),
        ("o1", "max_completion_tokens"),
        ("o1-mini", "max_completion_tokens"),
        ("o3", "max_completion_tokens"),
        ("o3-mini", "max_completion_tokens"),
        ("o4-mini", "max_completion_tokens"),
        ("openai/o3", "max_completion_tokens"),
        ("azure/o1-preview", "max_completion_tokens"),
    ])
    def test_token_kwarg_routing(self, model, expected_kwarg):
        from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

        adapter = OpenAIDirectAdapter.__new__(OpenAIDirectAdapter)
        adapter._client = mock_client
        adapter._model = model
        adapter._max_tokens = 1024
        adapter.last_usage = None

        exit_code = adapter.run("hello", _make_args())

        assert exit_code == 0
        _, kwargs = mock_client.chat.completions.create.call_args
        assert expected_kwarg in kwargs
        assert kwargs[expected_kwarg] == 1024
        # Make sure the *other* kwarg is not also being sent
        other = "max_tokens" if expected_kwarg == "max_completion_tokens" else "max_completion_tokens"
        assert other not in kwargs

    def test_is_reasoning_model_classifier(self):
        from promptpilot.adapters.openai_adapter import OpenAIDirectAdapter

        assert OpenAIDirectAdapter._is_reasoning_model("o1")
        assert OpenAIDirectAdapter._is_reasoning_model("o3-mini")
        assert OpenAIDirectAdapter._is_reasoning_model("O4-MINI")
        assert OpenAIDirectAdapter._is_reasoning_model("openai/o3")
        assert not OpenAIDirectAdapter._is_reasoning_model("gpt-4o")
        assert not OpenAIDirectAdapter._is_reasoning_model("gpt-5")


# ---------------------------------------------------------------------------
# Fix #5 — ShellToolAdapter stdin piping
# ---------------------------------------------------------------------------

class TestShellAdapterStdin:
    def test_build_command_stdin_mode_omits_prompt_from_argv(self):
        from promptpilot.adapters.shell import ShellToolAdapter

        adapter = ShellToolAdapter(
            tool_name="fake-tool",
            extra_args=["--flag"],
            use_stdin=True,
            stdin_sentinel="-",
        )
        with patch("promptpilot.adapters.shell.resolve_executable_name",
                   return_value="/usr/bin/fake-tool"):
            cmd = adapter.build_command("very long multi-line prompt" * 1000)

        assert cmd == ["/usr/bin/fake-tool", "--flag", "-"]
        # The actual prompt must never appear in argv
        assert all("multi-line prompt" not in tok for tok in cmd)

    def test_build_command_argv_mode_includes_prompt(self):
        from promptpilot.adapters.shell import ShellToolAdapter

        adapter = ShellToolAdapter(tool_name="fake-tool", extra_args=["-x"])
        with patch("promptpilot.adapters.shell.resolve_executable_name",
                   return_value="/usr/bin/fake-tool"):
            cmd = adapter.build_command("hi")

        assert cmd == ["/usr/bin/fake-tool", "-x", "hi"]

    def test_run_stdin_pipes_prompt_as_input(self):
        from promptpilot.adapters.shell import ShellToolAdapter

        adapter = ShellToolAdapter(
            tool_name="fake-tool", use_stdin=True, stdin_sentinel="-",
        )
        with patch("promptpilot.adapters.shell.resolve_executable_name",
                   return_value="/usr/bin/fake-tool"), \
             patch("promptpilot.adapters.shell.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0)
            exit_code = adapter.run("prompt body", _make_args(cwd=None))

        assert exit_code == 0
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "prompt body"
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        # stdin must not be DEVNULL when piping input
        assert "stdin" not in kwargs or kwargs.get("stdin") != -3

    def test_run_argv_mode_uses_devnull_stdin(self):
        from promptpilot.adapters.shell import ShellToolAdapter
        import subprocess as _sub

        adapter = ShellToolAdapter(tool_name="fake-tool")
        with patch("promptpilot.adapters.shell.resolve_executable_name",
                   return_value="/usr/bin/fake-tool"), \
             patch("promptpilot.adapters.shell.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0)
            adapter.run("hi", _make_args(cwd=None))

        _, kwargs = mock_run.call_args
        assert kwargs["stdin"] == _sub.DEVNULL
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"


# ---------------------------------------------------------------------------
# Fix #6 — Anthropic thinking/tool_use/redacted_thinking block handling
# ---------------------------------------------------------------------------

class TestAnthropicBlockHandling:
    def test_text_block_with_type_field_prints(self, capsys):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello world")],
            usage=_fake_usage(),
            stop_reason="end_turn",
        )
        exit_code = adapter.run("hi", _make_args())
        assert exit_code == 0
        assert "hello world" in capsys.readouterr().out

    def test_thinking_block_not_printed_to_stdout(self, capsys):
        """Thinking blocks must never leak to stdout (they'd corrupt tool pipes)."""
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="internal reasoning..."),
                SimpleNamespace(type="text", text="final answer"),
            ],
            usage=_fake_usage(),
            stop_reason="end_turn",
        )
        adapter.run("q", _make_args())
        out = capsys.readouterr().out
        assert "final answer" in out
        assert "internal reasoning" not in out

    def test_thinking_block_surfaced_on_verbose(self, capsys):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="chain-of-thought"),
                SimpleNamespace(type="text", text="ok"),
            ],
            usage=_fake_usage(),
            stop_reason="end_turn",
        )
        adapter.run("q", _make_args(verbose=True))
        captured = capsys.readouterr()
        assert "ok" in captured.out
        assert "chain-of-thought" in captured.err

    def test_redacted_thinking_does_not_crash(self, capsys):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type="redacted_thinking", data="***"),
                SimpleNamespace(type="text", text="done"),
            ],
            usage=_fake_usage(),
            stop_reason="end_turn",
        )
        exit_code = adapter.run("q", _make_args(verbose=True))
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "done" in captured.out
        assert "redacted" in captured.err

    def test_tool_use_only_response_warns(self, capsys):
        """If the model returns only tool_use (no text), warn via stderr."""
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type="tool_use", name="search_web", id="t1", input={}),
            ],
            usage=_fake_usage(),
            stop_reason="tool_use",
        )
        exit_code = adapter.run("q", _make_args())
        assert exit_code == 0
        err = capsys.readouterr().err
        assert "no text content" in err
        assert "tool_use" in err
        assert "search_web" in err

    def test_empty_content_warns_with_stop_reason(self, capsys):
        adapter = _make_anthropic_adapter()
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[],
            usage=_fake_usage(),
            stop_reason="max_tokens",
        )
        adapter.run("q", _make_args())
        err = capsys.readouterr().err
        assert "no text content" in err
        assert "max_tokens" in err

    def test_legacy_text_block_without_type_still_prints(self, capsys):
        """Older SDK versions may not set `type` on text blocks — fall back to hasattr."""
        adapter = _make_anthropic_adapter()
        # No `type` field — just `text`
        block = SimpleNamespace(text="legacy output")
        adapter._client.messages.create.return_value = SimpleNamespace(
            content=[block],
            usage=_fake_usage(),
            stop_reason="end_turn",
        )
        adapter.run("q", _make_args())
        assert "legacy output" in capsys.readouterr().out
