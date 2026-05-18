"""Collect git/filesystem metadata for the current repository."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from prpt.core.constants import LANGUAGE_MARKERS, TEST_FRAMEWORK_HINTS
from prpt.core.types import RepoMetadata
from prpt.core.utils import run_command


class RepoContextCollector:
    def collect(self, cwd: str) -> RepoMetadata:
        path = Path(cwd)
        changed = self._git_changed_files(cwd)
        return RepoMetadata(
            cwd=str(path.resolve()),
            branch=self._git_branch(cwd),
            changed_files=changed,
            diff=self._git_diff(cwd),
            dominant_language=self._dominant_language(path, changed),
            test_framework=self._detect_test_framework(path),
        )

    def _git_branch(self, cwd: str) -> Optional[str]:
        code, out, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        return out if code == 0 and out else None

    def _git_diff(self, cwd: str) -> Optional[str]:
        """Capture staged + unstaged diff as a single string."""
        parts: List[str] = []
        # Unstaged changes
        code, out, _ = run_command(["git", "diff"], cwd=cwd)
        if code == 0 and out:
            parts.append(out)
        # Staged changes
        code, out, _ = run_command(["git", "diff", "--cached"], cwd=cwd)
        if code == 0 and out:
            parts.append(out)
        return "\n".join(parts) if parts else None

    def _git_changed_files(self, cwd: str) -> List[str]:
        code, out, _ = run_command(["git", "status", "--porcelain"], cwd=cwd)
        if code != 0 or not out:
            return []
        return [line[3:].strip() for line in out.splitlines() if len(line) >= 4]

    def _dominant_language(self, path: Path, changed_files: List[str]) -> Optional[str]:
        candidates = changed_files[:]
        if not candidates:
            try:
                candidates = sorted(
                    str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()
                )[:300]
            except Exception:
                candidates = []
        counts: dict = {}
        for name in candidates:
            lang = LANGUAGE_MARKERS.get(Path(name).suffix.lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
        if not counts:
            return None
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _detect_test_framework(self, path: Path) -> Optional[str]:
        for marker in [
            "pytest.ini", "pyproject.toml", "package.json",
            "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js",
        ]:
            file_path = path / marker
            if not file_path.exists():
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue
            for hint, framework in TEST_FRAMEWORK_HINTS.items():
                if hint in content:
                    return framework
        return None
