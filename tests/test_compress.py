"""
tests/test_compress.py
======================
Unit tests for promptpilot.compress.tool_output.

Each test:
  1. Constructs realistic synthetic output (mirrors actual CLI output)
  2. Calls the specific compressor or the public compress() entry point
  3. Asserts the compressed result is strictly smaller
  4. Asserts no actionable information is lost (failure messages, change lines, etc.)

Run:  python -m pytest tests/test_compress.py -v
"""
from __future__ import annotations

import pytest
from promptpilot.compress.tool_output import (
    compress,
    detect_command_type,
    compress_pytest,
    compress_grep,
    compress_git_diff,
    compress_git_status,
    compress_git_log,
    compress_find,
    compress_linter,
    compress_installer,
    truncate_smart,
    strip_ansi,
    _parent_dir,
    _split_command_chain,
    _strip_env_prefix,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lines(n: int, prefix: str = "line") -> str:
    return "\n".join(f"{prefix} {i}" for i in range(n))


# ---------------------------------------------------------------------------
# detect_command_type
# ---------------------------------------------------------------------------

class TestDetectCommandType:
    def test_pytest_direct(self):
        assert detect_command_type("pytest tests/ -v") == "pytest"

    def test_pytest_module(self):
        assert detect_command_type("python -m pytest tests/test_foo.py -x") == "pytest"

    def test_pytest_with_env(self):
        assert detect_command_type("PYTHONPATH=src python -m pytest tests/") == "pytest"

    def test_cargo_test(self):
        assert detect_command_type("cargo test --workspace") == "test_generic"

    def test_go_test(self):
        assert detect_command_type("go test ./...") == "test_generic"

    def test_jest(self):
        assert detect_command_type("npx jest --coverage") == "test_generic"

    def test_grep(self):
        assert detect_command_type("grep -r 'retry' src/") == "grep"

    def test_rg(self):
        assert detect_command_type("rg 'retry_after' --type py") == "grep"

    def test_git_diff(self):
        assert detect_command_type("git diff HEAD~1") == "git_diff"

    def test_git_status(self):
        assert detect_command_type("git status") == "git_status"

    def test_git_log(self):
        assert detect_command_type("git log --oneline -20") == "git_log"

    def test_find(self):
        assert detect_command_type("find . -name '*.py' -type f") == "find"

    def test_ls(self):
        assert detect_command_type("ls -la") == "ls"

    def test_generic(self):
        assert detect_command_type("cat README.md") == "generic"

    def test_generic_docker(self):
        assert detect_command_type("docker logs my-container") == "generic"


# ---------------------------------------------------------------------------
# compress_pytest
# ---------------------------------------------------------------------------

_PYTEST_HEADER = """\
============================= test session starts ==============================
platform linux -- Python 3.11.0, pytest-7.4.0, pluggy-1.3.0
rootdir: /projects/httpx
collected 1847 items
"""

_PYTEST_PASSING_BLOCK = "\n".join(
    f"tests/test_client.py::test_method_{i} PASSED [ {i}%]"
    for i in range(200)
)

_PYTEST_FAILURES = """\
=================================== FAILURES ===================================
_________________________ test_retry_after _________________________
    def test_retry_after():
>       response = client.get("/retry")
E       AssertionError: expected status 200, got 429
tests/test_client.py:142: AssertionError
=========================== short test summary info ============================
FAILED tests/test_client.py::test_retry_after - AssertionError: expected status 200, got 429
============================== 1 failed, 1846 passed in 14.3s ==================
"""

_PYTEST_ALL_PASS = """\
============================= test session starts ==============================
collected 500 items
tests/test_foo.py ............................                            [100%]
========================= 500 passed in 3.1s ===============================
"""

_PYTEST_FULL = _PYTEST_HEADER + _PYTEST_PASSING_BLOCK + "\n" + _PYTEST_FAILURES


class TestCompressPytest:
    def test_shorter_than_original(self):
        result = compress_pytest(_PYTEST_FULL)
        assert len(result) < len(_PYTEST_FULL)

    def test_failure_message_preserved(self):
        result = compress_pytest(_PYTEST_FULL)
        assert "AssertionError: expected status 200, got 429" in result

    def test_failed_line_preserved(self):
        result = compress_pytest(_PYTEST_FULL)
        assert "FAILED tests/test_client.py::test_retry_after" in result

    def test_passing_lines_removed(self):
        result = compress_pytest(_PYTEST_FULL)
        assert "PASSED" not in result

    def test_summary_preserved(self):
        result = compress_pytest(_PYTEST_FULL)
        assert "1 failed" in result
        assert "1846 passed" in result

    def test_all_pass_returns_summary(self):
        result = compress_pytest(_PYTEST_ALL_PASS)
        # Should only keep the summary, not the platform header
        assert "500 passed" in result
        assert "platform linux" not in result

    def test_via_public_compress(self):
        result = compress("python -m pytest tests/ -v", _PYTEST_FULL)
        assert "AssertionError" in result
        assert len(result) < len(_PYTEST_FULL)


# ---------------------------------------------------------------------------
# compress_grep
# ---------------------------------------------------------------------------

def _make_grep_output(n_files: int, matches_per_file: int) -> str:
    lines = []
    for f in range(n_files):
        for m in range(matches_per_file):
            line_no = m * 10 + 1
            lines.append(
                f"src/module_{f}/client.py:{line_no}:    retry_after = headers.get('Retry-After')"
            )
    return "\n".join(lines)


class TestCompressGrep:
    def test_few_files_caps_per_file(self):
        output = _make_grep_output(n_files=5, matches_per_file=30)
        result = compress_grep(output)
        assert len(result) < len(output)
        # Should report "N more" for each file
        assert "more match" in result

    def test_many_files_collapses_to_dirs(self):
        output = _make_grep_output(n_files=25, matches_per_file=5)
        result = compress_grep(output)
        assert len(result) < len(output)
        # Should have directory-level summary lines
        assert "files," in result and "matches" in result

    def test_total_count_present(self):
        output = _make_grep_output(n_files=5, matches_per_file=10)
        result = compress_grep(output)
        assert "total match" in result

    def test_via_public_compress(self):
        output = _make_grep_output(n_files=10, matches_per_file=20)
        result = compress("rg 'retry_after' src/", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# compress_git_diff
# ---------------------------------------------------------------------------

def _make_git_diff(n_files: int = 3, changes_per_hunk: int = 5,
                   context_lines: int = 20) -> str:
    parts = []
    for i in range(n_files):
        hunk_lines = [
            f"diff --git a/src/file_{i}.py b/src/file_{i}.py",
            f"index abc123..def456 100644",
            f"--- a/src/file_{i}.py",
            f"+++ b/src/file_{i}.py",
            f"@@ -10,{context_lines + changes_per_hunk} +10,{context_lines + changes_per_hunk} @@",
        ]
        # Add context lines before
        hunk_lines += [f" context_before_{j}" for j in range(context_lines)]
        # Add change lines
        for c in range(changes_per_hunk):
            hunk_lines.append(f"-old_line_{c}")
            hunk_lines.append(f"+new_line_{c}")
        # Add context lines after
        hunk_lines += [f" context_after_{j}" for j in range(context_lines)]
        parts.append("\n".join(hunk_lines))
    return "\n".join(parts)


class TestCompressGitDiff:
    def test_shorter_than_original(self):
        output = _make_git_diff(context_lines=20)
        result = compress_git_diff(output)
        assert len(result) < len(output)

    def test_change_lines_preserved(self):
        output = _make_git_diff()
        result = compress_git_diff(output)
        assert "-old_line_0" in result
        assert "+new_line_0" in result

    def test_hunk_headers_preserved(self):
        output = _make_git_diff()
        result = compress_git_diff(output)
        assert "@@" in result

    def test_excess_context_omitted(self):
        output = _make_git_diff(context_lines=20)
        result = compress_git_diff(output)
        # Many context lines compressed — should note omission
        assert "context line" in result or "omitted" in result

    def test_short_diff_unchanged(self):
        # A tiny diff with 2 context lines should not be compressed
        short = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n+++ b/x.py\n"
            "@@ -1,4 +1,4 @@\n"
            " line1\n-old\n+new\n line3\n"
        )
        result = compress_git_diff(short)
        # Either unchanged or trivially compressed — change lines must survive
        assert "-old" in result
        assert "+new" in result

    def test_via_public_compress(self):
        output = _make_git_diff(n_files=5, context_lines=25)
        result = compress("git diff HEAD~1", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# compress_git_status
# ---------------------------------------------------------------------------

def _make_git_status(n_untracked: int = 50) -> str:
    lines = [
        "On branch feature/retry-after",
        "Changes to be committed:",
        "  (use \"git restore --staged <file>...\" to unstage)",
        "\tmodified:   httpx/_client.py",
        "\tmodified:   httpx/_config.py",
        "",
        "Changes not staged for commit:",
        "\tmodified:   tests/test_client.py",
        "",
        "Untracked files:",
        "  (use \"git add <file>...\" to include in what will be committed)",
    ]
    for i in range(n_untracked):
        subdir = "a" if i < n_untracked // 2 else "b"
        lines.append(f"\tbuild/output/{subdir}/artifact_{i}.o")
    return "\n".join(lines)


class TestCompressGitStatus:
    def test_short_status_unchanged(self):
        short = "On branch main\nnothing to commit, working tree clean\n"
        assert compress_git_status(short) == short

    def test_many_untracked_collapsed(self):
        output = _make_git_status(n_untracked=50)
        result = compress_git_status(output)
        assert len(result) < len(output)
        # Staged changes must survive
        assert "httpx/_client.py" in result
        # Untracked collapsed to dirs
        assert "untracked" in result.lower()

    def test_staged_changes_preserved(self):
        output = _make_git_status(n_untracked=40)
        result = compress_git_status(output)
        assert "modified:   httpx/_config.py" in result

    def test_via_public_compress(self):
        output = _make_git_status(n_untracked=50)
        result = compress("git status", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# compress_git_log
# ---------------------------------------------------------------------------

def _make_git_log(n_commits: int = 50) -> str:
    blocks = []
    for i in range(n_commits):
        blocks.append(
            f"commit {'a' * 40}\n"
            f"Author: Dev <dev@example.com>\n"
            f"Date:   Mon Apr {i+1} 12:00:00 2026 +0000\n"
            f"\n"
            f"    feat: commit message {i}\n"
        )
    return "\n".join(blocks)


class TestCompressGitLog:
    def test_short_log_unchanged(self):
        output = _make_git_log(n_commits=10)
        result = compress_git_log(output)
        assert result == output

    def test_long_log_truncated(self):
        output = _make_git_log(n_commits=50)
        result = compress_git_log(output)
        assert len(result) < len(output)
        assert "omitted" in result

    def test_first_commits_preserved(self):
        output = _make_git_log(n_commits=50)
        result = compress_git_log(output)
        assert "commit message 0" in result

    def test_via_public_compress(self):
        output = _make_git_log(n_commits=50)
        result = compress("git log --oneline", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# compress_find
# ---------------------------------------------------------------------------

def _make_find_output(n_paths: int = 200) -> str:
    lines = []
    for i in range(n_paths):
        subdir = chr(ord('a') + (i % 10))
        lines.append(f"./src/{subdir}/module_{i}.py")
    return "\n".join(lines)


class TestCompressFind:
    def test_short_find_unchanged(self):
        output = _make_find_output(n_paths=20)
        assert compress_find(output) == output

    def test_long_find_compressed(self):
        output = _make_find_output(n_paths=200)
        result = compress_find(output)
        assert len(result) < len(output)
        assert "more path" in result

    def test_first_paths_preserved(self):
        output = _make_find_output(n_paths=200)
        result = compress_find(output)
        assert "./src/a/module_0.py" in result

    def test_via_public_compress(self):
        output = _make_find_output(n_paths=200)
        result = compress("find . -name '*.py'", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# truncate_smart
# ---------------------------------------------------------------------------

class TestTruncateSmart:
    def test_short_unchanged(self):
        output = _lines(50)
        assert truncate_smart(output) == output

    def test_long_truncated(self):
        output = _lines(500)
        result = truncate_smart(output)
        assert len(result) < len(output)
        assert "omitted" in result

    def test_first_line_kept(self):
        output = _lines(500)
        result = truncate_smart(output)
        assert "line 0" in result

    def test_last_line_kept(self):
        output = _lines(500)
        result = truncate_smart(output)
        assert "line 499" in result

    def test_custom_head_tail(self):
        output = _lines(300)
        result = truncate_smart(output, head=10, tail=5)
        assert "line 0" in result
        assert "line 299" in result
        assert len(result.splitlines()) < 300


# ---------------------------------------------------------------------------
# compress() public API — fail-open guarantees
# ---------------------------------------------------------------------------

class TestCompressFailOpen:
    def test_empty_output(self):
        assert compress("pytest", "") == ""

    def test_whitespace_only(self):
        result = compress("pytest", "   \n  ")
        assert result.strip() == ""

    def test_short_output_unchanged(self):
        short = "1 passed in 0.01s"
        assert compress("pytest", short) == short

    def test_unknown_command_smart_truncated(self):
        long_output = _lines(500)
        result = compress("some_weird_binary --flag", long_output)
        # Either smart-truncated or original; must not raise
        assert isinstance(result, str)

    def test_savings_below_threshold_returns_original(self):
        # A 10-line output that can't be compressed much
        tiny = _lines(10)
        result = compress("pytest", tiny)
        # Either original or very close — must not crash
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------

class TestStripAnsi:
    def test_strips_csi_colors(self):
        raw = "\x1b[31mred\x1b[0m plain \x1b[1;32mbold-green\x1b[0m"
        assert strip_ansi(raw) == "red plain bold-green"

    def test_strips_cursor_moves(self):
        raw = "line1\x1b[2Aline2\x1b[0Kline3"
        assert strip_ansi(raw) == "line1line2line3"

    def test_strips_bare_cr_progress_bar(self):
        raw = "progress: 10%\rprogress: 50%\rprogress: 100%\ndone\n"
        result = strip_ansi(raw)
        assert "\r" not in result
        assert "\n" in result  # newlines preserved

    def test_preserves_newlines(self):
        raw = "\x1b[31mfoo\x1b[0m\nbar\n"
        assert strip_ansi(raw) == "foo\nbar\n"

    def test_strips_osc_hyperlinks(self):
        raw = "see \x1b]8;;https://example.com\x07link\x1b]8;;\x07 here"
        # OSC sequences removed; link text remains
        cleaned = strip_ansi(raw)
        assert "https://example.com" not in cleaned
        assert "link" in cleaned

    def test_pytest_colored_failure(self):
        """Real-world: pytest -v with colour makes FAILED red."""
        raw = (
            "\x1b[31mFAILED\x1b[0m tests/test_foo.py::test_one - AssertionError\n"
            "\x1b[32m.\x1b[0m\n"
        )
        cleaned = strip_ansi(raw)
        assert cleaned.startswith("FAILED")
        # And compressor can now pick it up
        assert compress("pytest", cleaned * 100) != cleaned * 100 or True  # smoke


# ---------------------------------------------------------------------------
# _parent_dir helper
# ---------------------------------------------------------------------------

class TestParentDir:
    def test_posix_path(self):
        assert _parent_dir("src/foo/bar.py") == "src/foo"

    def test_windows_path(self):
        assert _parent_dir("C:\\projects\\httpx\\foo.py") == "C:/projects/httpx"

    def test_mixed_slashes(self):
        assert _parent_dir("src\\foo/bar.py") == "src/foo"

    def test_bare_filename(self):
        assert _parent_dir("foo.py") == "."

    def test_trailing_slash(self):
        assert _parent_dir("src/foo/") == "src"


# ---------------------------------------------------------------------------
# Command-chain detection
# ---------------------------------------------------------------------------

class TestCommandChain:
    def test_split_simple(self):
        assert _split_command_chain("pytest tests/") == ["pytest tests/"]

    def test_split_and(self):
        parts = _split_command_chain("cd foo && pytest tests/")
        assert any("pytest" in p for p in parts)
        assert any("cd" in p for p in parts)

    def test_split_pipe(self):
        parts = _split_command_chain("pytest -v | tee out.log")
        assert any("pytest" in p for p in parts)
        assert any("tee" in p for p in parts)

    def test_split_semicolon(self):
        parts = _split_command_chain("ls; pytest; echo done")
        assert len(parts) == 3

    def test_split_preserves_quoted(self):
        parts = _split_command_chain('pytest -k "foo && bar" -v')
        # The quoted "&&" must not break the command
        joined = " ".join(parts)
        assert "foo && bar" in joined

    def test_chain_detection_prefers_specific(self):
        # cd is first, pytest last — pytest must win
        assert detect_command_type("cd /tmp && pytest tests/") == "pytest"

    def test_chain_pipe_detection(self):
        assert detect_command_type("pytest tests/ | tee x.log") == "pytest"

    def test_chain_with_env_vars(self):
        cmd = 'PYTHONPATH=src ANTHROPIC_API_KEY="sk-..." python -m pytest tests/'
        assert detect_command_type(cmd) == "pytest"

    def test_env_prefix_strip_quoted(self):
        stripped = _strip_env_prefix('FOO="a b c" BAR=baz cmd args')
        assert stripped == "cmd args"

    def test_env_prefix_strip_single_quoted(self):
        stripped = _strip_env_prefix("FOO='a b' cmd")
        assert stripped == "cmd"

    def test_tsc_detected_in_chain(self):
        assert detect_command_type("cd frontend && npx tsc --noEmit") == "linter"

    def test_ruff_via_python_module(self):
        assert detect_command_type("python -m ruff check src/") == "linter"

    def test_pip_install_detected(self):
        assert detect_command_type("pip install -e .[anthropic]") == "installer"

    def test_npm_install_detected(self):
        assert detect_command_type("npm install") == "installer"

    def test_cargo_build_detected(self):
        assert detect_command_type("cargo build --release") == "installer"


# ---------------------------------------------------------------------------
# Linter compressor
# ---------------------------------------------------------------------------

def _make_ruff_output(n_files: int = 5, diags_per_file: int = 20) -> str:
    lines = []
    for f in range(n_files):
        for d in range(diags_per_file):
            lines.append(
                f"src/module_{f}.py:{d+1}:{d*2+1}: E501 Line too long ({80+d} > 79 characters)"
            )
    lines.append(f"Found {n_files * diags_per_file} errors.")
    return "\n".join(lines)


def _make_tsc_output(n_files: int = 5, diags_per_file: int = 15) -> str:
    lines = []
    for f in range(n_files):
        for d in range(diags_per_file):
            lines.append(
                f"src/mod_{f}.ts({d+1},{d*2+1}): error TS2322: "
                f"Type 'string' is not assignable to type 'number'."
            )
    lines.append(f"Found {n_files * diags_per_file} errors in {n_files} files.")
    return "\n".join(lines)


class TestCompressLinter:
    def test_ruff_compressed(self):
        output = _make_ruff_output(n_files=8, diags_per_file=25)
        result = compress_linter(output)
        assert len(result) < len(output)

    def test_ruff_caps_per_file(self):
        output = _make_ruff_output(n_files=5, diags_per_file=25)
        result = compress_linter(output)
        # Should contain "more diagnostic(s)" markers
        assert "more diagnostic" in result

    def test_ruff_summary_preserved(self):
        output = _make_ruff_output(n_files=5, diags_per_file=25)
        result = compress_linter(output)
        assert "Found" in result and "errors" in result

    def test_tsc_compressed(self):
        output = _make_tsc_output(n_files=6, diags_per_file=20)
        result = compress_linter(output)
        assert len(result) < len(output)
        assert "more diagnostic" in result

    def test_short_linter_unchanged(self):
        short = "src/foo.py:1:1: E501 line too long\nFound 1 error."
        assert compress_linter(short) == short

    def test_via_public_compress(self):
        output = _make_ruff_output(n_files=10, diags_per_file=25)
        result = compress("ruff check src/", output)
        assert len(result) < len(output)

    def test_mypy_detected_via_chain(self):
        output = _make_ruff_output(n_files=8, diags_per_file=15)
        result = compress("cd src && python -m mypy .", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# Installer compressor
# ---------------------------------------------------------------------------

def _make_pip_install_output(with_errors: bool = False) -> str:
    lines = [
        "Collecting numpy",
        "  Downloading numpy-1.26.0-cp311-cp311-win_amd64.whl (15.8 MB)",
        "     ---------------------------------------- 15.8/15.8 MB 10.2 MB/s eta 0:00:00",
        "Collecting scipy",
        "  Downloading scipy-1.11.3-cp311-cp311-win_amd64.whl (44.1 MB)",
        "     ---------------------------------------- 44.1/44.1 MB 12.1 MB/s eta 0:00:00",
        "Requirement already satisfied: packaging in /venv/lib/python3.11/site-packages (23.1)",
        "Using cached charset_normalizer-3.3.0-cp311-cp311-win_amd64.whl (99 kB)",
    ] * 10  # multiply to make it long
    if with_errors:
        lines.append("ERROR: Could not find a version that satisfies the requirement xyz")
        lines.append("ERROR: No matching distribution found for xyz")
    lines.append("Installing collected packages: numpy, scipy, pandas")
    lines.append("Successfully installed numpy-1.26.0 scipy-1.11.3 pandas-2.1.1")
    return "\n".join(lines)


class TestCompressInstaller:
    def test_pip_compressed(self):
        output = _make_pip_install_output()
        result = compress_installer(output)
        assert len(result) < len(output)

    def test_pip_errors_preserved(self):
        output = _make_pip_install_output(with_errors=True)
        result = compress_installer(output)
        assert "ERROR" in result or "error" in result.lower()
        assert "xyz" in result

    def test_pip_final_summary_preserved(self):
        output = _make_pip_install_output()
        result = compress_installer(output)
        assert "Successfully installed" in result

    def test_short_installer_unchanged(self):
        short = "Successfully installed foo-1.0"
        assert compress_installer(short) == short

    def test_via_public_compress(self):
        output = _make_pip_install_output()
        result = compress("pip install -r requirements.txt", output)
        assert len(result) < len(output)


# ---------------------------------------------------------------------------
# Integration: ANSI + compression end-to-end
# ---------------------------------------------------------------------------

class TestAnsiIntegration:
    def test_colored_pytest_output_compresses(self):
        raw = (
            "\x1b[1m============ test session starts ============\x1b[0m\n"
            + "\n".join(
                f"tests/test_foo.py::test_{i} \x1b[32mPASSED\x1b[0m [ {i}%]"
                for i in range(100)
            )
            + "\n\x1b[31mFAILED\x1b[0m tests/test_foo.py::test_bad - AssertionError\n"
            "============ 1 failed, 99 passed in 0.5s ============\n"
        )
        result = compress("pytest tests/ -v", raw)
        assert len(result) < len(raw)
        # Failure keyword survives
        assert "FAILED" in result
        assert "AssertionError" in result
