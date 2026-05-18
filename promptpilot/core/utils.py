"""Shared utility functions: logging, subprocess, deduplication."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from promptpilot.core.constants import DEFAULT_LOG_FILE
from promptpilot.core.types import NormalizedRequest, RepoMetadata, TokenStats, ValidationResult


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def write_stderr(message: str) -> None:
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        stream.write(message + "\n")
    except Exception:
        pass


def unique_preserve_order(items: List[str]) -> List[str]:
    seen: set = set()
    result: List[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------

def run_command(cmd: Sequence[str], cwd: Optional[str] = None) -> tuple:
    try:
        # Explicit utf-8 + errors="replace" because Python 3.11 on Windows
        # otherwise defaults to the system ANSI codepage (gbk on some boxes)
        # and crashes the pipe reader thread on git output containing cp1252
        # bytes like 0x93/0x94 (smart quotes from git's pager/UI).
        proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              check=False)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "Command not found: {0}".format(cmd[0])


def preview_command(cmd: Sequence[str]) -> str:
    try:
        return shlex.join(list(cmd))
    except Exception:
        return " ".join(cmd)


# ---------------------------------------------------------------------------
# JSONL run log
# ---------------------------------------------------------------------------

def append_jsonl_log(log_path: str, event: dict) -> None:
    path = Path(log_path)
    try:
        if path.parent and str(path.parent) not in {"", "."}:
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        write_stderr("Failed to write log file {0}: {1}".format(log_path, exc))


def build_log_event(
    *,
    mode: str,
    tool: Optional[str],
    normalizer: Optional[str],
    cwd: Optional[str],
    repo: RepoMetadata,
    raw_prompt: str,
    final_prompt: str,
    exit_code: Optional[int],
    auto: bool = False,
    strict: bool = False,
    dry_run: bool = False,
    pass_through: bool = False,
    normalized: Optional[NormalizedRequest] = None,
    validation: Optional[ValidationResult] = None,
    token_stats: Optional[TokenStats] = None,
) -> dict:
    event: dict = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "tool": tool,
        "normalizer": normalizer,
        "cwd": cwd,
        "repo_branch": repo.branch,
        "repo_dominant_language": repo.dominant_language,
        "repo_test_framework": repo.test_framework,
        "repo_changed_files": repo.changed_files,
        "repo_has_diff": bool(repo.diff),
        "raw_prompt": raw_prompt,
        "final_prompt": final_prompt,
        "exit_code": exit_code,
        "auto": auto,
        "strict": strict,
        "dry_run": dry_run,
        "pass_through": pass_through,
    }
    if normalized is not None:
        event["normalized"] = asdict(normalized)
    if validation is not None:
        event["validation"] = asdict(validation)
    if token_stats is not None:
        event["token_stats"] = asdict(token_stats)
    return event


def maybe_log_run(log_path: str, enabled: bool, **kwargs) -> None:
    if not enabled:
        return
    event = build_log_event(**kwargs)
    append_jsonl_log(log_path, event)
