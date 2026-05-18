"""Shell / Codex adapters — forward prompt to a CLI tool via subprocess."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Sequence

from prpt.core.utils import preview_command, run_command, write_stderr
from prpt.adapters.echo import ToolAdapter

_SESSION_TTL = 300  # seconds to retain session file (5 min)


def _session_path(cwd: str) -> Path:
    key = hashlib.sha256(cwd.encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "promptpilot_session_{0}.json".format(key)


def save_session_files(cwd: str, modified: List[str]) -> None:
    """Persist the list of files Codex modified for the next promptpilot call."""
    try:
        _session_path(cwd).write_text(
            json.dumps({"ts": time.time(), "files": modified}),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_session_files(cwd: str) -> List[str]:
    """Return files from the last Codex session, or [] if stale/missing."""
    path = _session_path(cwd)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data["ts"] > _SESSION_TTL:
            path.unlink(missing_ok=True)
            return []
        return data.get("files", [])
    except Exception:
        return []


def _git_modified_files(cwd: str) -> List[str]:
    """Return files with unstaged or staged changes (what Codex likely touched)."""
    files: List[str] = []
    for git_args in [["git", "diff", "--name-only"], ["git", "diff", "--name-only", "--cached"]]:
        code, out, _ = run_command(git_args, cwd=cwd)
        if code == 0 and out:
            files.extend(out.splitlines())
    return list(dict.fromkeys(files))  # deduplicate, preserve order


def resolve_executable_name(tool_name: str) -> str:
    candidates = [tool_name]
    if os.name == "nt":
        # Prefer .exe over .cmd/.bat to avoid CVE-2024-3220 class vulnerabilities
        # (cmd.exe re-parses argv for .cmd/.bat, enabling command injection via
        # quotes/metachars in prompt text).
        candidates.extend([tool_name + ".exe", tool_name + ".cmd", tool_name + ".bat"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return tool_name


def build_codex_command(
    extra_args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> List[str]:
    extra_args = list(extra_args or [])
    codex_executable = resolve_executable_name("codex")
    # Strip "exec" if user accidentally passed it as a tool-arg
    if extra_args and extra_args[0] == "exec":
        extra_args = extra_args[1:]
    cmd: List[str] = [codex_executable, "exec"]
    if "--skip-git-repo-check" not in extra_args:
        cmd.append("--skip-git-repo-check")
    # Wire working directory via --cd (Codex's native flag)
    if cwd and "--cd" not in extra_args and "-C" not in extra_args:
        cmd.extend(["--cd", cwd])
    cmd.extend(extra_args)
    # Use "-" to signal: read prompt from stdin (avoids shell quoting issues
    # with long multi-line prompts).
    cmd.append("-")
    return cmd


class ShellToolAdapter(ToolAdapter):
    def __init__(
        self,
        tool_name: str,
        extra_args: Optional[List[str]] = None,
        use_stdin: bool = False,
        stdin_sentinel: Optional[str] = None,
    ):
        """
        Parameters
        ----------
        tool_name : str
            Name of the CLI tool to invoke (resolved via ``shutil.which``).
        extra_args : list[str], optional
            Additional argv passed to the tool before the prompt.
        use_stdin : bool
            If True, pipe ``final_prompt`` via stdin instead of argv. Avoids
            the Windows 8191-char argv limit and shell-quoting issues on
            multi-line prompts.
        stdin_sentinel : str, optional
            When ``use_stdin=True``, append this token to argv to signal the
            tool to read from stdin (e.g. ``"-"`` for many Unix tools).
        """
        self.tool_name = tool_name
        self.extra_args = extra_args or []
        self.use_stdin = use_stdin
        self.stdin_sentinel = stdin_sentinel
        self.last_modified_files: List[str] = []

    def build_command(self, final_prompt: str) -> List[str]:
        executable = resolve_executable_name(self.tool_name)
        if self.use_stdin:
            cmd = [executable] + self.extra_args
            if self.stdin_sentinel is not None:
                cmd.append(self.stdin_sentinel)
            return cmd
        return [executable] + self.extra_args + [final_prompt]

    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        cmd = self.build_command(final_prompt)
        exec_cwd = getattr(args, "cwd", None)
        if getattr(args, "verbose", False):
            write_stderr("[adapter] exec: {0}".format(preview_command(cmd)))
        try:
            if self.use_stdin:
                proc = subprocess.run(
                    cmd,
                    text=True,
                    input=final_prompt,
                    encoding="utf-8",
                    errors="replace",
                    cwd=exec_cwd,
                )
            else:
                proc = subprocess.run(
                    cmd,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    encoding="utf-8",
                    errors="replace",
                    cwd=exec_cwd,
                )
            # Capture files the downstream tool modified so loader's session
            # section 0 can surface them on the next invocation. Applies to
            # every shell adapter (claude-code, codex, any future tool).
            if exec_cwd and proc.returncode == 0:
                modified = _git_modified_files(exec_cwd)
                if modified:
                    save_session_files(exec_cwd, modified)
                    self.last_modified_files = modified
            return proc.returncode
        except FileNotFoundError:
            write_stderr(
                "Downstream tool not found: {0}. Tried: {1}".format(
                    self.tool_name, preview_command(cmd)
                )
            )
            return 127


class CodexAdapter(ShellToolAdapter):
    def __init__(self, extra_args: Optional[List[str]] = None):
        super().__init__(tool_name="codex", extra_args=extra_args)

    def build_command(self, cwd: Optional[str] = None) -> List[str]:
        return build_codex_command(self.extra_args, cwd=cwd)

    def run(self, final_prompt: str, args: argparse.Namespace) -> int:
        exec_cwd = getattr(args, "cwd", None)
        cmd = self.build_command(cwd=exec_cwd)
        if getattr(args, "verbose", False):
            write_stderr("[adapter] exec: {0}".format(preview_command(cmd)))
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                input=final_prompt,
                encoding="utf-8",
                errors="replace",
                cwd=exec_cwd,
            )
            if exec_cwd and proc.returncode == 0:
                modified = _git_modified_files(exec_cwd)
                if modified:
                    save_session_files(exec_cwd, modified)
                    self.last_modified_files = modified
            return proc.returncode
        except FileNotFoundError:
            write_stderr(
                "Downstream tool not found: {0}. Tried: {1}".format(
                    self.tool_name, preview_command(cmd)
                )
            )
            return 127
