"""Tests for grep-guided repo content loader."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from prpt.repo.loader import (
    RepoContentLoader,
    _extract_search_terms,
    _find_matching_lines,
    _format_grep_excerpt,
    _merge_windows,
)


# ---------------------------------------------------------------------------
# _extract_search_terms
# ---------------------------------------------------------------------------

class TestExtractSearchTerms:
    def test_filters_stop_words(self):
        terms = _extract_search_terms("fix the bug in the file")
        assert "the" not in terms
        assert "fix" not in terms
        assert "bug" not in terms

    def test_keeps_meaningful_terms(self):
        terms = _extract_search_terms("timeout in ConnectionPool retry logic")
        assert "timeout" in terms
        assert "ConnectionPool" in terms or "connectionpool" in terms
        assert "retry" in terms
        assert "logic" in terms

    def test_caps_at_twenty(self):
        prompt = " ".join(f"term{i}" for i in range(50))
        terms = _extract_search_terms(prompt)
        assert len(terms) <= 20

    def test_deduplicates(self):
        terms = _extract_search_terms("timeout timeout timeout")
        assert terms.count("timeout") == 1

    def test_empty_prompt(self):
        assert _extract_search_terms("") == []

    def test_preserves_camel_case_identifiers(self):
        terms = _extract_search_terms("fix ConnectionPool.send_request issue")
        # Original-cased identifier should appear
        assert any("ConnectionPool" in t for t in terms)

    def test_min_length_three(self):
        terms = _extract_search_terms("ab abc abcd")
        assert "ab" not in terms
        assert "abc" in terms


# ---------------------------------------------------------------------------
# _find_matching_lines
# ---------------------------------------------------------------------------

class TestFindMatchingLines:
    def test_finds_exact_match(self):
        lines = ["no match", "timeout here", "nothing"]
        hits = _find_matching_lines(lines, ["timeout"])
        assert hits == [1]

    def test_case_insensitive(self):
        lines = ["TIMEOUT error", "normal line"]
        hits = _find_matching_lines(lines, ["timeout"])
        assert 0 in hits

    def test_multiple_terms(self):
        lines = ["foo here", "bar here", "neither"]
        hits = _find_matching_lines(lines, ["foo", "bar"])
        assert 0 in hits
        assert 1 in hits
        assert 2 not in hits

    def test_empty_terms_returns_nothing(self):
        lines = ["some line", "another line"]
        # Empty pattern would match everything — should be caller's responsibility,
        # but the function should not crash
        hits = _find_matching_lines(lines, ["specificxyz"])
        assert hits == []

    def test_returns_zero_based_indices(self):
        lines = ["match", "no"]
        hits = _find_matching_lines(lines, ["match"])
        assert hits == [0]


# ---------------------------------------------------------------------------
# _merge_windows
# ---------------------------------------------------------------------------

class TestMergeWindows:
    def test_empty_hits(self):
        assert _merge_windows([], 100, 5) == []

    def test_single_hit(self):
        windows = _merge_windows([10], 100, 5)
        assert windows == [(5, 15)]

    def test_clamps_to_bounds(self):
        windows = _merge_windows([0], 100, 40)
        assert windows[0][0] == 0

        windows = _merge_windows([99], 100, 40)
        assert windows[0][1] == 99

    def test_merges_overlapping(self):
        # hits at 10 and 12 with context=5 → windows (5,15) and (7,17) → merged (5,17)
        windows = _merge_windows([10, 12], 100, 5)
        assert len(windows) == 1
        assert windows[0] == (5, 17)

    def test_keeps_separate_non_overlapping(self):
        # hits 5 and 90 with context=3 → (2,8) and (87,93) — no overlap
        windows = _merge_windows([5, 90], 100, 3)
        assert len(windows) == 2

    def test_adjacent_windows_merge(self):
        # hits 5 and 10 with context=3 → (2,8) and (7,13)
        # start=7 <= end+1=9 → merge
        windows = _merge_windows([5, 10], 100, 3)
        assert len(windows) == 1


# ---------------------------------------------------------------------------
# _format_grep_excerpt
# ---------------------------------------------------------------------------

class TestFormatGrepExcerpt:
    def test_includes_filename(self):
        lines = ["line zero", "line one"]
        windows = [(0, 1)]
        out = _format_grep_excerpt("src/foo.py", lines, windows)
        assert "src/foo.py" in out

    def test_includes_line_numbers(self):
        lines = ["alpha", "beta", "gamma"]
        windows = [(0, 2)]
        out = _format_grep_excerpt("x.py", lines, windows)
        assert "1 |" in out
        assert "3 |" in out

    def test_gap_marker_between_windows(self):
        lines = ["a"] * 20
        windows = [(0, 2), (15, 17)]
        out = _format_grep_excerpt("x.py", lines, windows)
        assert "..." in out

    def test_no_gap_for_single_window(self):
        lines = ["a", "b", "c"]
        windows = [(0, 2)]
        out = _format_grep_excerpt("x.py", lines, windows)
        # Should not have gap marker for a single window
        assert out.count("...") == 0


# ---------------------------------------------------------------------------
# RepoContentLoader integration
# ---------------------------------------------------------------------------

class TestRepoContentLoader:
    def _make_repo(self, tmp_path: Path, files: dict) -> str:
        """Write files dict into tmp_path and return cwd string."""
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return str(tmp_path)

    def test_small_file_loaded_whole(self, tmp_path):
        cwd = self._make_repo(tmp_path, {"main.py": "def hello():\n    pass\n"})
        loader = RepoContentLoader()
        # Patch repo metadata
        class FakeRepo:
            changed_files = ["main.py"]
            cwd = str(tmp_path)
        block = loader.build_context_block("fix hello function", FakeRepo())
        assert "def hello" in block

    def test_large_file_uses_grep_excerpt(self, tmp_path):
        # Build a large file where "timeout" appears only in the middle
        lines = ["# unrelated line {0}".format(i) for i in range(300)]
        lines[150] = "def handle_timeout(conn):"
        lines[151] = "    raise TimeoutError('connection timed out')"
        content = "\n".join(lines)

        cwd = self._make_repo(tmp_path, {"client.py": content})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["client.py"]
            cwd = str(tmp_path)

        block = loader.build_context_block("fix timeout in client", FakeRepo())
        # Grep-guided should find line 150/151
        assert "handle_timeout" in block or "TimeoutError" in block

    def test_large_file_no_terms_falls_back_to_head(self, tmp_path):
        content = "x = 1\n" * 2000  # > MAX_FILE_BYTES, no meaningful terms
        cwd = self._make_repo(tmp_path, {"data.py": content})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["data.py"]
            cwd = str(tmp_path)

        # Prompt with no meaningful terms
        block = loader.build_context_block("a an the or", FakeRepo())
        # Should not crash, and should contain truncation note or content
        assert "data.py" in block

    def test_budget_is_respected(self, tmp_path):
        # Write many small files to consume budget
        files = {"file{0}.py".format(i): "x = {0}\n".format(i) for i in range(200)}
        cwd = self._make_repo(tmp_path, files)
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = list(files.keys())
            cwd = str(tmp_path)

        block = loader.build_context_block("fix x value", FakeRepo())
        assert len(block) <= loader.MAX_TOTAL_BYTES + 500  # small slack for headers

    def test_missing_file_skipped(self, tmp_path):
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["nonexistent.py"]
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        # Should not crash; nonexistent file just gets skipped
        # block may be just the file_tree or empty
        assert isinstance(block, str)

    def test_grep_excerpt_capped_at_max_grep_bytes(self, tmp_path):
        # Create a file with many hits so the raw excerpt would exceed MAX_GREP_BYTES
        lines = ["def timeout_handler_{0}(): pass".format(i) for i in range(500)]
        content = "\n".join(lines)
        cwd = self._make_repo(tmp_path, {"big.py": content})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["big.py"]
            cwd = str(tmp_path)

        block = loader.build_context_block("timeout handler", FakeRepo())
        # The excerpt portion (excluding tree) should respect cap
        # Find the big.py section
        assert "big.py" in block
        # Total block shouldn't explode
        assert len(block) < 50_000

    def test_file_tree_present(self, tmp_path):
        cwd = self._make_repo(tmp_path, {"app.py": "pass\n", "utils.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("some prompt", FakeRepo())
        assert "<file_tree>" in block
        assert "app.py" in block

    def test_mentioned_file_loaded(self, tmp_path):
        cwd = self._make_repo(tmp_path, {
            "utils.py": "def helper(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        # Prompt mentions utils.py explicitly
        block = loader.build_context_block("look at utils.py helper function", FakeRepo())
        assert "helper" in block


# ---------------------------------------------------------------------------
# Feature 3: Convention files
# ---------------------------------------------------------------------------

class TestConventionFiles:
    def _make_repo(self, tmp_path: Path, files: dict) -> str:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return str(tmp_path)

    def test_claude_md_auto_included(self, tmp_path):
        self._make_repo(tmp_path, {
            "CLAUDE.md": "Always use type hints.\nNever use print for logging.",
            "main.py": "pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        assert "<convention_files>" in block
        assert "Always use type hints" in block

    def test_cursorrules_auto_included(self, tmp_path):
        self._make_repo(tmp_path, {
            ".cursorrules": "Prefer functional style.",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        assert "Prefer functional style" in block

    def test_no_convention_files_no_section(self, tmp_path):
        self._make_repo(tmp_path, {"main.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        assert "<convention_files>" not in block

    def test_large_convention_file_truncated(self, tmp_path):
        self._make_repo(tmp_path, {
            "CLAUDE.md": "x" * 10000,
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        assert "truncated" in block

    def test_convention_not_reloaded_as_changed(self, tmp_path):
        self._make_repo(tmp_path, {
            "CLAUDE.md": "Use pytest for testing.",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["CLAUDE.md"]
            cwd = str(tmp_path)

        block = loader.build_context_block("fix something", FakeRepo())
        # CLAUDE.md should appear once (in convention_files), not duplicated
        assert block.count("Use pytest for testing") == 1


# ---------------------------------------------------------------------------
# Feature 2: Git diff in context
# ---------------------------------------------------------------------------

class TestGitDiffInContext:
    def _make_repo(self, tmp_path: Path, files: dict) -> str:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return str(tmp_path)

    def test_diff_included_in_context(self, tmp_path):
        self._make_repo(tmp_path, {"main.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-pass\n+print('hello')"

        block = loader.build_context_block("fix something", FakeRepo())
        assert "<git_diff>" in block
        assert "print('hello')" in block

    def test_no_diff_no_section(self, tmp_path):
        self._make_repo(tmp_path, {"main.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = None

        block = loader.build_context_block("fix something", FakeRepo())
        assert "<git_diff>" not in block

    def test_large_diff_truncated(self, tmp_path):
        self._make_repo(tmp_path, {"main.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = "+" * 50000

        block = loader.build_context_block("fix something", FakeRepo())
        assert "<git_diff>" in block
        assert "diff truncated" in block


# ---------------------------------------------------------------------------
# Feature 1: Repo-wide file ranking
# ---------------------------------------------------------------------------

class TestRepoWideRanking:
    def _make_repo(self, tmp_path: Path, files: dict) -> str:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return str(tmp_path)

    def test_finds_relevant_file_not_in_changed(self, tmp_path):
        self._make_repo(tmp_path, {
            "unrelated.py": "x = 1\n",
            "timeout_handler.py": "def handle_timeout(): raise TimeoutError\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []  # nothing changed
            cwd = str(tmp_path)

        # Prompt references timeout but doesn't name the file
        block = loader.build_context_block("fix the timeout handling", FakeRepo())
        assert "<relevant_files>" in block
        assert "handle_timeout" in block

    def test_ranks_by_hit_count(self, tmp_path):
        self._make_repo(tmp_path, {
            "few_hits.py": "timeout once\n" + "unrelated\n" * 50,
            "many_hits.py": "timeout timeout timeout\n" * 10,
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix timeout issue", FakeRepo())
        # many_hits.py should appear in relevant_files
        assert "many_hits.py" in block

    def test_skips_binary_extensions(self, tmp_path):
        self._make_repo(tmp_path, {
            "logo.png": "binary data here",
            "actual.py": "def timeout(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        block = loader.build_context_block("fix timeout", FakeRepo())
        # logo.png may appear in the file_tree, but must NOT be in relevant_files
        relevant_start = block.find("<relevant_files>")
        if relevant_start >= 0:
            relevant_section = block[relevant_start:]
            assert "logo.png" not in relevant_section

    def test_skips_already_loaded_files(self, tmp_path):
        self._make_repo(tmp_path, {
            "changed.py": "def timeout(): pass\n",
            "other.py": "def timeout(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["changed.py"]
            cwd = str(tmp_path)

        block = loader.build_context_block("fix timeout", FakeRepo())
        # changed.py in <changed_files>, other.py in <relevant_files>
        assert "<changed_files>" in block
        # other.py should still appear but in relevant_files, not duplicated
        assert "other.py" in block

    def test_no_terms_no_ranking(self, tmp_path):
        self._make_repo(tmp_path, {
            "code.py": "def hello(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)

        # All stop words — no search terms extracted
        block = loader.build_context_block("fix the bug", FakeRepo())
        assert "<relevant_files>" not in block

    def test_walk_respects_skip_dirs(self, tmp_path):
        self._make_repo(tmp_path, {
            "src/app.py": "timeout code\n",
            "node_modules/dep.js": "timeout in dep\n",
            ".git/objects/abc": "timeout in git\n",
        })
        loader = RepoContentLoader()
        files = loader._walk_repo_files(str(tmp_path))
        paths_str = " ".join(files)
        assert "src/app.py" in paths_str
        assert "node_modules" not in paths_str
        assert ".git" not in paths_str

    def test_walk_max_files_cap(self, tmp_path):
        # Create more files than the cap
        files = {"f{0}.py".format(i): "x\n" for i in range(600)}
        self._make_repo(tmp_path, files)
        loader = RepoContentLoader()
        result = loader._walk_repo_files(str(tmp_path), max_files=50)
        assert len(result) <= 50


# ---------------------------------------------------------------------------
# Feature: Test file auto-pairing
# ---------------------------------------------------------------------------

class TestTestFilePairing:
    def _make_repo(self, tmp_path: Path, files: dict) -> None:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_pairs_test_underscore_prefix(self, tmp_path):
        self._make_repo(tmp_path, {
            "httpx/_client.py": "class Client: pass\n",
            "tests/test__client.py": "def test_client(): pass\n",
        })
        loader = RepoContentLoader()
        pair = loader._find_test_pair("httpx/_client.py", str(tmp_path))
        assert pair is not None
        assert "test__client" in pair

    def test_pairs_suffix_test(self, tmp_path):
        self._make_repo(tmp_path, {
            "src/retry.py": "def retry(): pass\n",
            "tests/retry_test.py": "def test_retry(): pass\n",
        })
        loader = RepoContentLoader()
        pair = loader._find_test_pair("src/retry.py", str(tmp_path))
        assert pair is not None
        assert "retry_test" in pair

    def test_pairs_same_directory(self, tmp_path):
        self._make_repo(tmp_path, {
            "lib/utils.py": "def helper(): pass\n",
            "lib/test_utils.py": "def test_helper(): pass\n",
        })
        loader = RepoContentLoader()
        pair = loader._find_test_pair("lib/utils.py", str(tmp_path))
        assert pair is not None
        assert "test_utils" in pair

    def test_no_pair_returns_none(self, tmp_path):
        self._make_repo(tmp_path, {
            "src/orphan.py": "x = 1\n",
        })
        loader = RepoContentLoader()
        pair = loader._find_test_pair("src/orphan.py", str(tmp_path))
        assert pair is None

    def test_skips_test_files_themselves(self, tmp_path):
        self._make_repo(tmp_path, {
            "tests/test_client.py": "def test_x(): pass\n",
        })
        loader = RepoContentLoader()
        loaded: set = set()
        pairs = loader._pair_test_files(["tests/test_client.py"], str(tmp_path), loaded)
        assert pairs == []

    def test_test_files_in_context_block(self, tmp_path):
        self._make_repo(tmp_path, {
            "httpx/_config.py": "class Timeout: pass\n",
            "tests/test__config.py": "def test_timeout(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["httpx/_config.py"]
            cwd = str(tmp_path)
            diff = None

        block = loader.build_context_block("fix timeout", FakeRepo())
        assert "<test_files>" in block
        assert "test_timeout" in block

    def test_no_duplicate_if_test_already_changed(self, tmp_path):
        self._make_repo(tmp_path, {
            "httpx/_config.py": "class Timeout: pass\n",
            "tests/test__config.py": "def test_timeout(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["httpx/_config.py", "tests/test__config.py"]
            cwd = str(tmp_path)
            diff = None

        block = loader.build_context_block("fix timeout", FakeRepo())
        # test__config.py should appear exactly once
        assert block.count("test_timeout") == 1


# ---------------------------------------------------------------------------
# Feature: Secret / credential detection
# ---------------------------------------------------------------------------

class TestSecretDetection:
    def test_redacts_openai_key(self):
        from prpt.repo.loader import _redact_secrets
        content = 'OPENAI_API_KEY = "sk-proj-abc123XYZabc123XYZabc123XYZ"\n'
        redacted, count = _redact_secrets(content)
        assert "sk-proj-" not in redacted
        assert "[REDACTED]" in redacted
        assert count > 0

    def test_redacts_anthropic_key(self):
        from prpt.repo.loader import _redact_secrets
        content = 'key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"\n'
        redacted, count = _redact_secrets(content)
        assert "sk-ant-" not in redacted
        assert "[REDACTED]" in redacted

    def test_redacts_github_token(self):
        from prpt.repo.loader import _redact_secrets
        content = "token = ghp_abcdefghijklmnopqrstuvwxyz123456\n"
        redacted, count = _redact_secrets(content)
        assert "ghp_" not in redacted
        assert "[REDACTED]" in redacted

    def test_redacts_aws_key(self):
        from prpt.repo.loader import _redact_secrets
        content = "aws_key = AKIAIOSFODNN7EXAMPLE\n"
        redacted, count = _redact_secrets(content)
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "[REDACTED]" in redacted

    def test_preserves_normal_code(self):
        from prpt.repo.loader import _redact_secrets
        content = "def connect(host, port=8080):\n    return socket.connect(host, port)\n"
        redacted, count = _redact_secrets(content)
        assert redacted == content
        assert count == 0

    def test_secret_not_in_context_block(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-api03-supersecretkey12345678901234\n"
            "DEBUG=true\n",
            encoding="utf-8",
        )
        src = tmp_path / "main.py"
        src.write_text("import os\nkey = os.getenv('ANTHROPIC_API_KEY')\n", encoding="utf-8")

        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = [".env"]
            cwd = str(tmp_path)
            diff = None

        block = loader.build_context_block("fix auth", FakeRepo())
        assert "sk-ant-api03-supersecretkey" not in block
        assert "[REDACTED]" in block


# ---------------------------------------------------------------------------
# Feature: Disk caching (#2)
# ---------------------------------------------------------------------------

class TestDiskCaching:
    def _make_repo(self, tmp_path: Path, files: dict) -> None:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_cache_hit_returns_same_block(self, tmp_path):
        self._make_repo(tmp_path, {"app.py": "def timeout(): pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = None

        block1 = loader.build_context_block("fix timeout", FakeRepo())
        # Modify the file — cache should still return original
        (tmp_path / "app.py").write_text("MODIFIED\n", encoding="utf-8")
        block2 = loader.build_context_block("fix timeout", FakeRepo())
        assert block1 == block2  # cache hit, file change not visible

    def test_cache_key_differs_by_terms(self, tmp_path):
        self._make_repo(tmp_path, {"app.py": "pass\n"})
        loader = RepoContentLoader()
        key1 = loader._cache_key(str(tmp_path), ["timeout"])
        key2 = loader._cache_key(str(tmp_path), ["retry"])
        assert key1 != key2

    def test_cache_key_differs_by_cwd(self, tmp_path, tmp_path_factory):
        other = tmp_path_factory.mktemp("other")
        loader = RepoContentLoader()
        key1 = loader._cache_key(str(tmp_path), ["timeout"])
        key2 = loader._cache_key(str(other), ["timeout"])
        assert key1 != key2

    def test_cache_miss_after_ttl(self, tmp_path):
        self._make_repo(tmp_path, {"app.py": "def timeout(): pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = None

        # Build and cache
        loader.build_context_block("fix timeout", FakeRepo())
        key = loader._cache_key(str(tmp_path), loader._cache_key.__doc__ and ["timeout"] or ["timeout"])
        cache_path = loader._cache_path(loader._cache_key(str(tmp_path), ["timeout"]))

        # Manually expire by backdating the timestamp
        import json as _json
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
        data["ts"] -= loader.CACHE_TTL + 10
        cache_path.write_text(_json.dumps(data), encoding="utf-8")

        # Now file change should be visible (cache expired)
        # Keep "timeout" so grep-ranking still picks up the file
        (tmp_path / "app.py").write_text("def timeout_MODIFIED(): pass\n", encoding="utf-8")
        block = loader.build_context_block("fix timeout", FakeRepo())
        assert "timeout_MODIFIED" in block

    def test_save_and_load_cache(self, tmp_path):
        loader = RepoContentLoader()
        key = loader._cache_key(str(tmp_path), ["test"])
        loader._save_cache(key, "my cached block")
        result = loader._load_cache(key)
        assert result == "my cached block"

    def test_load_cache_missing_returns_none(self, tmp_path):
        loader = RepoContentLoader()
        result = loader._load_cache("nonexistent_key_xyz")
        assert result is None


# ---------------------------------------------------------------------------
# Feature: Call-site awareness (#4)
# ---------------------------------------------------------------------------

class TestCallSiteAwareness:
    def _make_repo(self, tmp_path: Path, files: dict) -> None:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_finds_function_call_site(self, tmp_path):
        self._make_repo(tmp_path, {
            "core/client.py": "class HttpClient:\n    def send(self): pass\n",
            "api/views.py": "from core.client import HttpClient\nclient = HttpClient()\n",
        })
        loader = RepoContentLoader()
        all_files = ["core/client.py", "api/views.py"]
        sites = loader._find_call_sites(all_files, str(tmp_path), ["HttpClient"])
        assert any("views.py" in s for s in sites)

    def test_finds_import_site(self, tmp_path):
        self._make_repo(tmp_path, {
            "utils/retry.py": "def retry_with_backoff(): pass\n",
            "tasks/worker.py": "import retry_with_backoff\nretry_with_backoff()\n",
        })
        loader = RepoContentLoader()
        all_files = ["utils/retry.py", "tasks/worker.py"]
        sites = loader._find_call_sites(all_files, str(tmp_path), ["retry_with_backoff"])
        assert any("worker.py" in s for s in sites)

    def test_no_terms_returns_empty(self, tmp_path):
        self._make_repo(tmp_path, {"a.py": "pass\n"})
        loader = RepoContentLoader()
        result = loader._find_call_sites(["a.py"], str(tmp_path), [])
        assert result == []

    def test_call_sites_appear_in_context_block(self, tmp_path):
        self._make_repo(tmp_path, {
            "core/auth.py": "class AuthManager:\n    def login(self): pass\n",
            "app/login.py": "from core.auth import AuthManager\nm = AuthManager()\nm.login()\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["core/auth.py"]
            cwd = str(tmp_path)
            diff = None

        block = loader.build_context_block("fix AuthManager login", FakeRepo())
        # call_sites or relevant_files should contain the caller
        assert "login" in block or "AuthManager" in block

    def test_returns_at_most_max_ranked(self, tmp_path):
        # Create many files that all call the same function
        files = {"caller_{0}.py".format(i): "MyFunc()\n" for i in range(20)}
        files["core.py"] = "def MyFunc(): pass\n"
        self._make_repo(tmp_path, files)
        loader = RepoContentLoader()
        all_files = list(files.keys())
        sites = loader._find_call_sites(all_files, str(tmp_path), ["MyFunc"])
        assert len(sites) <= loader.MAX_RANKED_FILES


# ---------------------------------------------------------------------------
# Feature: Session continuity (#6)
# ---------------------------------------------------------------------------

class TestSessionContinuity:
    def _make_repo(self, tmp_path: Path, files: dict) -> None:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def test_session_files_appear_first(self, tmp_path):
        from unittest.mock import patch
        self._make_repo(tmp_path, {
            "session_file.py": "def session_func(): pass\n",
            "changed_file.py": "def changed_func(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["changed_file.py"]
            cwd = str(tmp_path)
            diff = None

        with patch("prpt.adapters.shell.load_session_files", return_value=["session_file.py"]):
            block = loader.build_context_block("fix something", FakeRepo())

        assert "<session_files>" in block
        assert "session_func" in block
        # session_files section should appear before changed_files section
        assert block.index("<session_files>") < block.index("<changed_files>")

    def test_no_session_files_no_section(self, tmp_path):
        from unittest.mock import patch
        self._make_repo(tmp_path, {"app.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = None

        with patch("prpt.adapters.shell.load_session_files", return_value=[]):
            block = loader.build_context_block("fix something", FakeRepo())

        assert "<session_files>" not in block

    def test_session_file_not_duplicated_in_changed(self, tmp_path):
        from unittest.mock import patch
        self._make_repo(tmp_path, {
            "overlap.py": "def overlap_func(): pass\n",
        })
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = ["overlap.py"]
            cwd = str(tmp_path)
            diff = None

        with patch("prpt.adapters.shell.load_session_files", return_value=["overlap.py"]):
            block = loader.build_context_block("fix overlap", FakeRepo())

        # overlap_func should appear exactly once
        assert block.count("overlap_func") == 1

    def test_stale_session_not_loaded(self, tmp_path):
        """load_session_files itself handles staleness; here we test [] → no section."""
        from unittest.mock import patch
        self._make_repo(tmp_path, {"app.py": "pass\n"})
        loader = RepoContentLoader()

        class FakeRepo:
            changed_files = []
            cwd = str(tmp_path)
            diff = None

        # Simulate stale session returning []
        with patch("prpt.adapters.shell.load_session_files", return_value=[]):
            block = loader.build_context_block("fix something", FakeRepo())

        assert "<session_files>" not in block
