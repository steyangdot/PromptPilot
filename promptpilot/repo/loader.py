"""Load relevant repo file contents to ground the SLM rewrite."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

from promptpilot.core.types import RepoMetadata
from promptpilot.core.utils import run_command


# Words too generic to use as search terms
_STOP_WORDS: Set[str] = {
    "the", "a", "an", "and", "or", "in", "on", "at", "to", "for",
    "of", "with", "is", "it", "be", "are", "was", "were", "has",
    "have", "not", "no", "do", "does", "did", "when", "where",
    "what", "how", "why", "if", "fix", "add", "get", "set", "use",
    "make", "run", "put", "let", "can", "will", "that", "this",
    "from", "into", "by", "as", "but", "so", "than", "then",
    "bug", "code", "file", "line", "function", "method", "class",
    "should", "would", "could", "check", "return", "value", "test",
}

# Context lines to load around each matching line
_CONTEXT_LINES = 40

# Project convention files — always auto-included when present
_CONVENTION_FILES = [
    "CLAUDE.md", ".cursorrules", "AGENTS.md", "AI_CONTEXT.md",
    ".github/copilot-instructions.md", "CONVENTIONS.md", "CODING_GUIDELINES.md",
]

# Patterns that match credentials/secrets — redacted before entering context
_SECRET_PATTERNS: List[re.Pattern] = [
    # Generic high-entropy assignments: API_KEY=sk-abc123...
    re.compile(r'(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token'
               r'|private[_-]?key|client[_-]?secret)\s*[=:]\s*["\']?([A-Za-z0-9+/\-_]{16,})["\']?'),
    # Anthropic keys: sk-ant-...
    re.compile(r'\bsk-ant-[A-Za-z0-9\-_]{20,}'),
    # OpenAI keys: sk-proj-... or sk-...
    re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9\-_]{20,}'),
    # GitHub tokens: ghp_, gho_, github_pat_
    re.compile(r'\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}'),
    # AWS keys: AKIA...
    re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    # Generic .env value lines: KEY=long_value
    re.compile(r'(?m)^([A-Z_]{4,})\s*=\s*(["\']?)([A-Za-z0-9+/\-_]{20,})\2\s*$'),
    # Bearer tokens in headers
    re.compile(r'(?i)bearer\s+[A-Za-z0-9\-_.~+/]{20,}'),
    # Password assignments
    re.compile(r'(?i)password\s*[=:]\s*["\']?([^\s"\']{8,})["\']?'),
]


def _redact_secrets(content: str) -> tuple[str, int]:
    """Redact secrets from file content. Returns (redacted_content, count)."""
    count = 0
    for pattern in _SECRET_PATTERNS:
        def _replace(m: re.Match) -> str:
            nonlocal count
            count += 1
            # Keep the key name if captured, replace only the value
            if m.lastindex and m.lastindex >= 2:
                prefix = m.group(0)[: m.start(m.lastindex) - m.start()]
                return prefix + "[REDACTED]"
            return "[REDACTED]"
        content = pattern.sub(_replace, content)
    return content, count


# Extensions that are binary / not useful as code context
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".class",
    ".db", ".sqlite", ".sqlite3",
    ".lock", ".min.js", ".min.css", ".map",
}


def _extract_search_terms(prompt: str) -> List[str]:
    """Pull meaningful search terms out of a prompt for grep-guided loading."""
    # Split on whitespace and punctuation, lowercase
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*", prompt.lower())
    # Also capture camelCase / snake_case identifiers as-is for exact matching
    identifiers = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", prompt)

    seen: Set[str] = set()
    terms: List[str] = []

    for tok in raw_tokens:
        if len(tok) >= 3 and tok not in _STOP_WORDS and tok not in seen:
            seen.add(tok)
            terms.append(tok)

    # Preserve original-cased identifiers (function/class names).
    # Skip stop words. Skip exact duplicates (already in seen as-is).
    # But DO add "ConnectionPool" even if "connectionpool" is already there —
    # the original casing matters for grep matching.
    for ident in identifiers:
        lower = ident.lower()
        if lower in _STOP_WORDS:
            continue
        if ident in seen:
            continue  # exact form already present
        seen.add(lower)
        seen.add(ident)
        terms.append(ident)

    return terms[:20]  # cap at 20 terms


def _find_matching_lines(lines: List[str], terms: List[str]) -> List[int]:
    """Return 0-based line indices where any search term appears."""
    pattern = re.compile(
        "|".join(re.escape(t) for t in terms),
        re.IGNORECASE,
    )
    return [i for i, line in enumerate(lines) if pattern.search(line)]


def _merge_windows(
    hit_indices: List[int], total_lines: int, context: int
) -> List[Tuple[int, int]]:
    """Convert hit line indices into (start, end) windows, merging overlaps."""
    if not hit_indices:
        return []

    windows: List[Tuple[int, int]] = []
    for idx in hit_indices:
        start = max(0, idx - context)
        end = min(total_lines - 1, idx + context)
        if windows and start <= windows[-1][1] + 1:
            # Extend the previous window instead of creating a new one
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def _format_grep_excerpt(rel_path: str, lines: List[str], windows: List[Tuple[int, int]]) -> str:
    """Format grep-guided excerpt with line numbers and gap markers."""
    parts: List[str] = []
    for i, (start, end) in enumerate(windows):
        if i > 0:
            parts.append("...")
        chunk = lines[start: end + 1]
        # Prefix each line with its 1-based line number
        numbered = "\n".join(
            "{:4d} | {}".format(start + j + 1, line.rstrip())
            for j, line in enumerate(chunk)
        )
        parts.append(numbered)

    body = "\n".join(parts)
    return "### {0} (grep-guided: lines shown)\n```\n{1}\n```".format(rel_path, body)


class RepoContentLoader:
    MAX_FILE_BYTES = 6_000     # small-file threshold; large files use grep-guided
    MAX_TOTAL_BYTES = 32_000   # total context block budget
    MAX_TREE_ENTRIES = 150
    # Grep-guided excerpt: max bytes we allow per large file
    MAX_GREP_BYTES = 12_000
    _SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
        "dist", "build", ".next", ".nuxt", "target", "out", ".mypy_cache",
    }

    MAX_RANKED_FILES = 5  # how many repo-wide ranked files to load
    CACHE_TTL = 60        # seconds before disk cache is invalidated

    # ------------------------------------------------------------------
    # Disk cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, cwd: str, terms: List[str], scope: str = "localized") -> str:
        """Build a cache key from repo path, git HEAD, search terms, and scope."""
        code, head, _ = run_command(["git", "rev-parse", "HEAD"], cwd=cwd)
        git_hash = head if code == 0 and head else "no-git"
        raw = "{cwd}|{git}|{terms}|{scope}".format(
            cwd=cwd, git=git_hash, terms=",".join(sorted(terms)), scope=scope
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _cache_path(self, key: str) -> Path:
        return Path(tempfile.gettempdir()) / "promptpilot_ctx_{0}.json".format(key)

    def _load_cache(self, key: str) -> Optional[str]:
        path = self._cache_path(key)
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data["ts"] > self.CACHE_TTL:
                path.unlink(missing_ok=True)
                return None
            return data["block"]
        except Exception:
            return None

    def _save_cache(self, key: str, block: str) -> None:
        try:
            self._cache_path(key).write_text(
                json.dumps({"ts": time.time(), "block": block}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def build_context_block(
        self, prompt: str, repo: RepoMetadata, scope: str = "localized"
    ) -> str:
        """Build the context block, gated by scope.

        Scope gates:
          pinpoint / localized / explain → full block (all sections)
          broad                          → sections 0-4 only (file tree + changed files)
          new                            → section 1 only (file tree)
        """
        terms = _extract_search_terms(prompt)

        # Check disk cache first (scope is part of the key)
        cache_key = self._cache_key(repo.cwd, terms, scope)
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        parts: List[str] = []
        loaded: Set[str] = set()

        # Derived flags — avoids repeated string comparisons below
        _load_changed = scope != "new"           # sections 0, 2, 3, 4
        _load_full    = scope not in ("broad", "new")  # sections 4b-7

        # 0. Session continuity — files from last Codex/shell run (highest priority)
        if _load_changed:
            try:
                from promptpilot.adapters.shell import load_session_files
                session_files = [f for f in load_session_files(repo.cwd) if f not in loaded]
                if session_files:
                    budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
                    session_block = self._load_files(session_files, repo.cwd, budget, terms)
                    if session_block:
                        parts.append("<session_files>\n{0}\n</session_files>".format(session_block))
                    loaded.update(session_files)
            except Exception:
                pass

        # 1. File tree (always — every scope needs orientation)
        tree = self._file_tree(repo.cwd)
        if tree:
            parts.append("<file_tree>\n{0}\n</file_tree>".format(tree))

        # 2. Convention files (CLAUDE.md, .cursorrules, etc.) — high priority
        if _load_changed:
            convention_block, convention_paths = self._load_convention_files(repo.cwd)
            if convention_block:
                parts.append("<convention_files>\n{0}\n</convention_files>".format(convention_block))
                loaded.update(convention_paths)

        # 3. Git diff (staged + unstaged) — highest signal for active work
        if _load_changed:
            diff = getattr(repo, "diff", None)
            if diff:
                budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
                diff_budget = min(8000, budget // 3)
                diff_text = diff[:diff_budget]
                if len(diff) > diff_budget:
                    diff_text += "\n... (diff truncated)"
                parts.append("<git_diff>\n{0}\n</git_diff>".format(diff_text))

        # 4. Changed files (from git status)
        if _load_changed:
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            changed = [f for f in repo.changed_files if f not in loaded]
            changed_block = self._load_files(changed, repo.cwd, budget, terms)
            if changed_block:
                parts.append("<changed_files>\n{0}\n</changed_files>".format(changed_block))
            loaded.update(repo.changed_files)

        # 4b. Test pairs for changed files — skipped for broad/new
        if _load_full:
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            if budget > 500:
                changed_tests = self._pair_test_files(repo.changed_files, repo.cwd, loaded)
                if changed_tests:
                    test_block = self._load_files(changed_tests, repo.cwd, budget, terms)
                    if test_block:
                        parts.append("<test_files>\n{0}\n</test_files>".format(test_block))

        # 5. Mentioned files — skipped for broad/new
        if _load_full:
            mentioned = [f for f in self._find_mentioned_files(prompt, repo.cwd) if f not in loaded]
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            if mentioned and budget > 500:
                mentioned_block = self._load_files(mentioned, repo.cwd, budget, terms)
                if mentioned_block:
                    parts.append("<mentioned_files>\n{0}\n</mentioned_files>".format(mentioned_block))
                loaded.update(mentioned)

            # 5b. Test pairs for mentioned files
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            if budget > 500 and mentioned:
                mentioned_tests = self._pair_test_files(mentioned, repo.cwd, loaded)
                if mentioned_tests:
                    test_block = self._load_files(mentioned_tests, repo.cwd, budget, terms)
                    if test_block:
                        parts.append("<test_files>\n{0}\n</test_files>".format(test_block))

        # 6. Repo-wide ranked files — skipped for broad/new
        if _load_full:
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            if budget > 500 and terms:
                all_files = self._walk_repo_files(repo.cwd)
                candidates = [f for f in all_files if f not in loaded]
                ranked = self._rank_files_by_grep(candidates, repo.cwd, terms)
                if ranked:
                    ranked_block = self._load_files(ranked, repo.cwd, budget, terms)
                    if ranked_block:
                        parts.append("<relevant_files>\n{0}\n</relevant_files>".format(ranked_block))
                    loaded.update(ranked)

        # 7. Call-site awareness — skipped for broad/new
        if _load_full:
            budget = self.MAX_TOTAL_BYTES - sum(len(p) for p in parts)
            if budget > 500 and terms:
                all_files = self._walk_repo_files(repo.cwd)
                call_candidates = [f for f in all_files if f not in loaded]
                call_sites = self._find_call_sites(call_candidates, repo.cwd, terms)
                if call_sites:
                    call_block = self._load_files(call_sites, repo.cwd, budget, terms)
                    if call_block:
                        parts.append("<call_sites>\n{0}\n</call_sites>".format(call_block))

        block = "\n\n".join(parts)
        self._save_cache(cache_key, block)
        return block

    def _file_tree(self, cwd: str) -> str:
        entries: List[str] = []
        base = Path(cwd)
        try:
            for root, dirs, files in os.walk(cwd):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in self._SKIP_DIRS)
                depth = len(Path(root).relative_to(base).parts)
                if depth > 3:
                    dirs.clear()
                    continue
                indent = "  " * depth
                if depth > 0:
                    entries.append("{0}{1}/".format(indent, Path(root).name))
                for f in sorted(files):
                    entries.append("{0}  {1}".format(indent, f))
                if len(entries) >= self.MAX_TREE_ENTRIES:
                    entries.append("  ... (truncated)")
                    dirs.clear()
        except Exception:
            pass
        return "\n".join(entries)

    def _load_files(
        self, rel_paths: List[str], cwd: str, budget: int, terms: List[str]
    ) -> str:
        parts: List[str] = []
        remaining = budget
        for rel_path in rel_paths:
            if remaining <= 200:
                break
            abs_path = Path(cwd) / rel_path
            if not abs_path.is_file():
                continue
            try:
                raw = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            raw, _redacted = _redact_secrets(raw)

            if len(raw) <= self.MAX_FILE_BYTES:
                # Small file — load whole thing
                block = "### {0}\n```\n{1}\n```".format(rel_path, raw)
            else:
                # Large file — grep-guided excerpt
                block = self._grep_excerpt(rel_path, raw, terms)

            if len(block) > remaining:
                # Try a hard truncation as a last resort
                block = block[: remaining - 20] + "\n... (truncated)"
            parts.append(block)
            remaining -= len(block)
        return "\n\n".join(parts)

    def _grep_excerpt(self, rel_path: str, raw: str, terms: List[str]) -> str:
        """Extract relevant sections of a large file using search terms."""
        lines = raw.splitlines()

        if terms:
            hit_indices = _find_matching_lines(lines, terms)
        else:
            hit_indices = []

        if not hit_indices:
            # No hits — fall back to loading the first MAX_FILE_BYTES
            truncated = raw[: self.MAX_FILE_BYTES]
            return "### {0}\n```\n{1}\n... (truncated — no grep hits)\n```".format(
                rel_path, truncated
            )

        windows = _merge_windows(hit_indices, len(lines), _CONTEXT_LINES)

        excerpt = _format_grep_excerpt(rel_path, lines, windows)

        # Cap at MAX_GREP_BYTES to stay within budget
        if len(excerpt) > self.MAX_GREP_BYTES:
            # Keep only the first N windows that fit
            for n in range(len(windows) - 1, 0, -1):
                candidate = _format_grep_excerpt(rel_path, lines, windows[:n])
                if len(candidate) <= self.MAX_GREP_BYTES:
                    return candidate
            # Single window fallback
            return _format_grep_excerpt(rel_path, lines, windows[:1])

        return excerpt

    # ------------------------------------------------------------------
    # Convention files
    # ------------------------------------------------------------------

    def _load_convention_files(self, cwd: str) -> Tuple[str, List[str]]:
        """Load project convention files (CLAUDE.md, .cursorrules, etc.)."""
        max_per_file = 4000
        parts: List[str] = []
        loaded_paths: List[str] = []
        for name in _CONVENTION_FILES:
            abs_path = Path(cwd) / name
            if not abs_path.is_file():
                continue
            try:
                raw = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(raw) > max_per_file:
                raw = raw[:max_per_file] + "\n... (truncated)"
            parts.append("### {0}\n```\n{1}\n```".format(name, raw))
            loaded_paths.append(name)
        return "\n\n".join(parts), loaded_paths

    # ------------------------------------------------------------------
    # Repo-wide file ranking
    # ------------------------------------------------------------------

    def _walk_repo_files(self, cwd: str, max_depth: int = 4, max_files: int = 500) -> List[str]:
        """Walk the repo tree and return relative paths of text files."""
        base = Path(cwd)
        result: List[str] = []
        try:
            for root, dirs, files in os.walk(cwd):
                dirs[:] = sorted(
                    d for d in dirs
                    if not d.startswith(".") and d not in self._SKIP_DIRS
                )
                depth = len(Path(root).relative_to(base).parts)
                if depth > max_depth:
                    dirs.clear()
                    continue
                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    ext = Path(f).suffix.lower()
                    if ext in _BINARY_EXTENSIONS:
                        continue
                    rel = str(Path(root, f).relative_to(base)).replace("\\", "/")
                    result.append(rel)
                    if len(result) >= max_files:
                        dirs.clear()
                        return result
        except Exception:
            pass
        return result

    def _rank_files_by_grep(
        self, rel_paths: List[str], cwd: str, terms: List[str],
    ) -> List[str]:
        """Rank files by search-term hit count, return top K paths."""
        if not terms:
            return []
        max_read_bytes = 100_000
        scored: List[Tuple[int, float, str]] = []
        for rel_path in rel_paths:
            abs_path = Path(cwd) / rel_path
            try:
                size = abs_path.stat().st_size
                if size > max_read_bytes or size == 0:
                    continue
                raw = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines = raw.splitlines()
            hits = _find_matching_lines(lines, terms)
            if not hits:
                continue
            hit_count = len(hits)
            density = hit_count / max(len(lines), 1)
            scored.append((hit_count, density, rel_path))

        # Sort by hit count descending, density as tiebreaker
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [path for _, _, path in scored[:self.MAX_RANKED_FILES]]

    def _find_call_sites(
        self, rel_paths: List[str], cwd: str, terms: List[str],
    ) -> List[str]:
        """Find files that CALL or IMPORT the identified symbols (not just mention them)."""
        if not terms:
            return []

        # Build call/import patterns: identifier(  |  import identifier  |  from X import identifier
        identifiers = [t for t in terms if len(t) >= 4 and t[0].isupper() or "_" in t]
        if not identifiers:
            identifiers = [t for t in terms if len(t) >= 5]
        if not identifiers:
            return []

        call_pattern = re.compile(
            "|".join(
                r"(?:{id}\s*\(|import\s+{id}|from\s+\S+\s+import\s+(?:\S+,\s*)*{id})".format(
                    id=re.escape(ident)
                )
                for ident in identifiers
            )
        )

        max_read_bytes = 100_000
        scored: List[Tuple[int, str]] = []
        for rel_path in rel_paths:
            abs_path = Path(cwd) / rel_path
            try:
                size = abs_path.stat().st_size
                if size > max_read_bytes or size == 0:
                    continue
                raw = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            hits = len(call_pattern.findall(raw))
            if hits > 0:
                scored.append((hits, rel_path))

        scored.sort(key=lambda t: -t[0])
        return [path for _, path in scored[:self.MAX_RANKED_FILES]]

    # ------------------------------------------------------------------
    # Test file auto-pairing
    # ------------------------------------------------------------------

    def _find_test_pair(self, rel_path: str, cwd: str) -> str | None:
        """Given a source file, return its test file path if it exists."""
        p = Path(rel_path)
        stem = p.stem
        suffix = p.suffix
        parent = p.parent

        # Candidate test file names
        candidates = [
            "test_{stem}{suffix}".format(stem=stem, suffix=suffix),
            "{stem}_test{suffix}".format(stem=stem, suffix=suffix),
            "test_{stem}.py".format(stem=stem),
            "{stem}_test.py".format(stem=stem),
        ]

        # Candidate test directories (alongside or under tests/)
        test_dirs = [
            parent,                        # same dir: src/foo.py -> src/test_foo.py
            Path("tests") / parent,        # tests/src/test_foo.py
            Path("tests"),                 # tests/test_foo.py
            Path("test"),                  # test/test_foo.py
            Path("spec"),                  # spec/test_foo.py (JS/Ruby style)
        ]

        base = Path(cwd)
        for test_dir in test_dirs:
            for name in candidates:
                candidate = test_dir / name
                if (base / candidate).is_file():
                    return str(candidate).replace("\\", "/")
        return None

    def _pair_test_files(self, rel_paths: List[str], cwd: str, loaded: Set[str]) -> List[str]:
        """Return test file paths for each source file, skipping already-loaded ones."""
        pairs: List[str] = []
        for rel_path in rel_paths:
            # Skip files that are already test files
            name = Path(rel_path).name
            if name.startswith("test_") or name.endswith("_test.py") or "/test" in rel_path:
                continue
            test_path = self._find_test_pair(rel_path, cwd)
            if test_path and test_path not in loaded:
                pairs.append(test_path)
                loaded.add(test_path)  # prevent duplicates if multiple sources share a test
        return pairs

    # ------------------------------------------------------------------
    # Mentioned files
    # ------------------------------------------------------------------

    def _find_mentioned_files(self, prompt: str, cwd: str) -> List[str]:
        candidates = re.findall(r"[A-Za-z0-9_./\\-]+\.[A-Za-z]{1,6}", prompt)
        found: List[str] = []
        for candidate in candidates:
            if (Path(cwd) / candidate).is_file():
                found.append(candidate)
        return found
