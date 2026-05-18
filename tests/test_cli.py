"""Tests for CLI argument parsing and main flow."""
from __future__ import annotations

import pytest

from promptpilot.cli import parse_args, main


class TestParseArgs:
    def test_minimal_prompt(self):
        args = parse_args(["fix", "the", "bug"])
        assert args.prompt == ["fix", "the", "bug"]
        assert args.normalizer == "heuristic"
        assert args.tool == "echo"
        assert args.dry_run is False

    def test_dry_run_flag(self):
        args = parse_args(["--dry-run", "fix the bug"])
        assert args.dry_run is True

    def test_normalizer_slm(self):
        args = parse_args(["--normalizer", "slm", "add dark mode"])
        assert args.normalizer == "slm"

    def test_normalizer_slm_openai(self):
        args = parse_args(["--normalizer", "slm-openai", "add dark mode"])
        assert args.normalizer == "slm-openai"

    def test_tool_anthropic(self):
        args = parse_args(["--tool", "anthropic", "--model", "claude-opus-4-6", "fix bug"])
        assert args.tool == "anthropic"
        assert args.model == "claude-opus-4-6"

    def test_pass_through(self):
        args = parse_args(["--pass-through", "raw prompt text"])
        assert args.pass_through is True

    def test_install_hook_subcommand(self):
        args = parse_args(["install-hook"])
        assert args.subcommand == "install-hook"

    def test_install_hook_global(self):
        args = parse_args(["install-hook", "--global"])
        assert args.global_hook is True

    def test_max_tokens(self):
        args = parse_args(["--max-tokens", "8192", "do something"])
        assert args.max_tokens == 8192

    def test_tool_arg_repeatable(self):
        args = parse_args(["--tool-arg=--flag1", "--tool-arg=--flag2", "do it"])
        assert args.tool_arg == ["--flag1", "--flag2"]

    def test_theme_dark(self):
        args = parse_args(["--theme", "dark", "add dark mode"])
        assert args.theme == "dark"


class TestMainFlow:
    def test_empty_prompt_returns_2(self, capsys):
        exit_code = main([])
        assert exit_code == 2

    def test_dry_run_heuristic(self, capsys):
        exit_code = main(["--dry-run", "fix the timeout bug in the payment service"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "REVIEW" in output
        assert "FINAL PROMPT" in output

    def test_pass_through_dry_run(self, capsys):
        exit_code = main(["--pass-through", "--dry-run", "raw prompt here"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "raw prompt here" in output

    def test_echo_adapter(self, capsys):
        exit_code = main(["--tool", "echo", "fix the bug in auth"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert len(output) > 0

    def test_show_json(self, capsys):
        exit_code = main(["--dry-run", "--show-json", "fix the timeout bug in payments"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert '"task_type"' in output

    def test_dry_run_dark_theme(self, capsys):
        exit_code = main(["--dry-run", "--theme", "dark", "fix the timeout bug in payments"])
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "\x1b[" in output


class TestResolveRoute:
    """v2 control plane: _resolve_route picks {answer, act, clarify, passthrough}
    from the v2 spec when available, falls back to intent-derived choice for v1.
    """

    def test_v1_intent_act_returns_act(self):
        from promptpilot.cli import _resolve_route

        class _V1Norm:
            _last_intent = "act"
            _last_scope = "localized"
            # no _last_spec attribute (v1)

        assert _resolve_route(_V1Norm()) == "act"

    def test_v1_intent_explain_returns_answer(self):
        from promptpilot.cli import _resolve_route

        class _V1Norm:
            _last_intent = "explain"
            _last_scope = "new"

        assert _resolve_route(_V1Norm()) == "answer"

    def test_v1_no_intent_defaults_to_act(self):
        from promptpilot.cli import _resolve_route

        class _V1Norm:
            pass  # neither intent nor spec set

        assert _resolve_route(_V1Norm()) == "act"

    def test_v2_spec_route_passthrough_honored(self):
        from promptpilot.cli import _resolve_route
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_intent = "act"
            _last_scope = "pinpoint"
            _last_spec = ExecutionSpec(route="passthrough", intent="act", scope="pinpoint",
                                       downstream_prompt="x")

        assert _resolve_route(_V2Norm()) == "passthrough"

    def test_v2_spec_route_clarify_honored(self):
        from promptpilot.cli import _resolve_route
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_intent = "act"
            _last_scope = "localized"
            _last_spec = ExecutionSpec(route="clarify", intent="act", scope="localized",
                                       downstream_prompt="What did you mean by X?")

        assert _resolve_route(_V2Norm()) == "clarify"

    def test_v2_spec_route_overrides_intent_fallback(self):
        """If v2 spec says route=act but intent=explain, the spec wins.
        Future case: SLM produced an explanation-style prompt but routed it
        as actionable because the user explicitly asked for code."""
        from promptpilot.cli import _resolve_route
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_intent = "explain"  # would derive "answer" for v1
            _last_scope = "new"
            _last_spec = ExecutionSpec(route="act", intent="explain", scope="new",
                                       downstream_prompt="x")

        assert _resolve_route(_V2Norm()) == "act"

    def test_v2_spec_with_invalid_route_falls_back_to_intent(self):
        from promptpilot.cli import _resolve_route
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_intent = "explain"
            _last_scope = "new"
            # Force an invalid route by bypassing normalize_enums
            _last_spec = ExecutionSpec.__new__(ExecutionSpec)
            _last_spec.route = "garbage"
            _last_spec.intent = "explain"
            _last_spec.scope = "new"

        assert _resolve_route(_V2Norm()) == "answer"  # falls back to intent-derived


class TestRouteDispatch:
    """Integration-ish: main() honors route=passthrough by sending raw prompt
    unmodified, and route=clarify by printing the question and exiting 0.
    Uses dry-run / echo to avoid real API calls."""

    def test_passthrough_uses_raw_prompt_unmodified(self, capsys, monkeypatch):
        """When the normalizer reports route=passthrough, the dispatch
        substitutes raw_prompt for the SLM rewrite. Verified via --dry-run
        showing the raw prompt as the final prompt."""
        from promptpilot.core.spec import ExecutionSpec
        from promptpilot.normalizers.heuristic import HeuristicNormalizer

        # Patch HeuristicNormalizer to fake a v2 spec saying passthrough
        original_normalize = HeuristicNormalizer.normalize

        def fake_normalize(self, prompt, repo, high_stakes=False):
            result = original_normalize(self, prompt, repo, high_stakes=high_stakes)
            self._last_intent = "act"
            self._last_scope = "pinpoint"
            self._last_spec = ExecutionSpec(
                route="passthrough", intent="act", scope="pinpoint",
                downstream_prompt="(unused for passthrough)",
            )
            return result

        monkeypatch.setattr(HeuristicNormalizer, "normalize", fake_normalize)
        # Also need _last_intent/_last_scope to be readable on the instance
        monkeypatch.setattr(HeuristicNormalizer, "_last_intent", "act", raising=False)
        monkeypatch.setattr(HeuristicNormalizer, "_last_scope", "pinpoint", raising=False)
        monkeypatch.setattr(HeuristicNormalizer, "_last_spec", None, raising=False)

        exit_code = main(["--dry-run", "raw user prompt verbatim"])
        out = capsys.readouterr().out
        # FINAL PROMPT block should contain the raw prompt unmodified
        assert exit_code == 0
        assert "raw user prompt verbatim" in out

    def test_clarify_prints_question_and_exits_0(self, capsys, monkeypatch):
        """When the normalizer reports route=clarify, the dispatch prints the
        question (downstream_prompt) to stdout, logs the run, and exits 0
        without invoking the adapter."""
        from promptpilot.core.spec import ExecutionSpec
        from promptpilot.normalizers.heuristic import HeuristicNormalizer

        clarify_question = "Which payment service did you mean, Stripe or PayPal?"

        original_normalize = HeuristicNormalizer.normalize

        def fake_normalize(self, prompt, repo, high_stakes=False):
            result = original_normalize(self, prompt, repo, high_stakes=high_stakes)
            # Mutate the result so normalized_prompt carries the clarify question
            result.normalized_prompt = clarify_question
            self._last_intent = "act"
            self._last_scope = "localized"
            self._last_spec = ExecutionSpec(
                route="clarify", intent="act", scope="localized",
                downstream_prompt=clarify_question,
            )
            return result

        monkeypatch.setattr(HeuristicNormalizer, "normalize", fake_normalize)
        monkeypatch.setattr(HeuristicNormalizer, "_last_intent", "act", raising=False)
        monkeypatch.setattr(HeuristicNormalizer, "_last_scope", "localized", raising=False)
        monkeypatch.setattr(HeuristicNormalizer, "_last_spec", None, raising=False)

        # Not --dry-run, not --auto, not --compare -> clarify branch fires
        exit_code = main(["fix the bug in payments"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert clarify_question in out


class TestBuildAssistantRecord:
    """v2 roadmap #4: assistant session-turn record merges spec.memory_record
    (pre-run intent summary) with adapter.last_modified_files (post-run ground
    truth). v1 normalizers without `_last_spec` fall back to the SLM rewrite,
    preserving the prior `"Modified: {files}\\n{rewrite}"` behavior exactly."""

    @staticmethod
    def _fake_normalized(rewrite: str):
        from types import SimpleNamespace
        return SimpleNamespace(normalized_prompt=rewrite)

    def test_v2_memory_record_with_modified_files(self):
        from promptpilot.cli import _build_assistant_record
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_spec = ExecutionSpec(
                route="act", intent="act", scope="pinpoint",
                downstream_prompt="Fix the timeout in httpx/_client.py.",
                memory_record="User wants timeout fix in sync client; preserve public API.",
            )

        out = _build_assistant_record(
            _V2Norm(),
            self._fake_normalized("a much longer SLM rewrite that we do NOT want to see here"),
            ["httpx/_client.py", "httpx/_config.py"],
        )
        assert out.startswith("Modified: httpx/_client.py, httpx/_config.py\n")
        assert "User wants timeout fix in sync client" in out
        # memory_record won; the verbose rewrite must not appear
        assert "much longer SLM rewrite" not in out

    def test_v2_memory_record_no_modified_files(self):
        from promptpilot.cli import _build_assistant_record
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_spec = ExecutionSpec(
                route="answer", intent="explain", scope="pinpoint",
                downstream_prompt="N/A",
                memory_record="User asked what HTTP/2 multiplexing is.",
            )

        out = _build_assistant_record(
            _V2Norm(),
            self._fake_normalized("the SLM's explanation rewrite"),
            [],
        )
        assert out == "User asked what HTTP/2 multiplexing is."
        assert "Modified:" not in out

    def test_v2_empty_memory_record_falls_back_to_rewrite(self):
        """When memory_record is blank/whitespace, the helper falls back to
        the SLM rewrite to avoid storing an empty turn. Important for
        forward-compat: if the SLM produces a valid spec but omits the
        memory_record field, we still get a useful session record."""
        from promptpilot.cli import _build_assistant_record
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_spec = ExecutionSpec(
                route="act", intent="act", scope="pinpoint",
                downstream_prompt="x",
                memory_record="   ",  # whitespace only
            )

        out = _build_assistant_record(
            _V2Norm(),
            self._fake_normalized("the SLM rewrite stands in for memory_record"),
            ["a.py"],
        )
        assert out == "Modified: a.py\nthe SLM rewrite stands in for memory_record"

    def test_v1_no_spec_with_modified_files_preserves_current_behavior(self):
        """v1 normalizers without `_last_spec` must produce the same
        `"Modified: {files}\\n{rewrite[:400]}"` shape as before item #4."""
        from promptpilot.cli import _build_assistant_record

        class _V1Norm:
            pass  # no _last_spec, no memory_record

        out = _build_assistant_record(
            _V1Norm(),
            self._fake_normalized("Fix the timeout in httpx by raising the default."),
            ["httpx/_client.py"],
        )
        assert out == "Modified: httpx/_client.py\nFix the timeout in httpx by raising the default."

    def test_v1_no_spec_no_modified_files_preserves_current_behavior(self):
        from promptpilot.cli import _build_assistant_record

        class _V1Norm:
            pass

        out = _build_assistant_record(
            _V1Norm(), self._fake_normalized("Explain what HTTP/2 multiplexing is."), [],
        )
        assert out == "Explain what HTTP/2 multiplexing is."

    def test_truncates_files_list_to_eight_with_overflow_marker(self):
        from promptpilot.cli import _build_assistant_record

        class _V1Norm:
            pass

        files = ["f{0}.py".format(i) for i in range(10)]
        out = _build_assistant_record(_V1Norm(), self._fake_normalized("rewrite"), files)
        assert "f0.py, f1.py, f2.py, f3.py, f4.py, f5.py, f6.py, f7.py, ... (+2 more)" in out
        assert "f8.py" not in out
        assert "f9.py" not in out

    def test_total_length_capped_at_600_chars(self):
        """Long memory_record + many modified files must still fit in 600
        chars so MAX_TURNS history doesn't bloat the SLM context window."""
        from promptpilot.cli import _build_assistant_record
        from promptpilot.core.spec import ExecutionSpec

        class _V2Norm:
            _last_spec = ExecutionSpec(
                route="act", intent="act", scope="broad",
                downstream_prompt="x",
                memory_record="X" * 5000,  # comically long
            )

        out = _build_assistant_record(
            _V2Norm(), self._fake_normalized("y"), ["a.py", "b.py", "c.py"],
        )
        assert len(out) <= 600

    def test_v2_with_modified_truncates_memory_record_to_400(self):
        """When modified-files prefix is present, the summary tail is capped
        at 400 chars (the prefix needs room). Without modified files, the
        summary gets the full 600. This mirrors the v1 layout."""
        from promptpilot.cli import _build_assistant_record
        from promptpilot.core.spec import ExecutionSpec

        long_record = "A" * 500
        class _V2Norm:
            _last_spec = ExecutionSpec(
                route="act", intent="act", scope="pinpoint",
                downstream_prompt="x", memory_record=long_record,
            )

        with_files = _build_assistant_record(
            _V2Norm(), self._fake_normalized("ignored"), ["a.py"],
        )
        # "Modified: a.py\n" prefix + 400 'A's = 14 + 400 = 414
        assert with_files.count("A") == 400

        without_files = _build_assistant_record(
            _V2Norm(), self._fake_normalized("ignored"), [],
        )
        # Without prefix, summary gets the full 500 chars (cap is 600)
        assert without_files.count("A") == 500
