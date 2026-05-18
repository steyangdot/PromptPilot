"""Subprocess helpers — utf-8 enforcement + claude.exe orphan management.

Two recurring bug patterns this module exists to prevent:

1. **Windows GBK encoding crash** when subprocess.run is called with `text=True`
   but no explicit `encoding=`. Python 3.11 on Windows defaults to the locale
   encoding (often GBK), which fails on non-ASCII bytes from many tools.
   `safe_run()` enforces utf-8/replace whenever text mode is implied.

2. **claude.exe zombie accumulation.** When a chain run crashes or is Ctrl-C'd
   with a claude.exe subprocess in flight, the orphan keeps retrying API calls,
   competes for the API key's concurrent-request quota, and (~10+ orphans)
   eventually exhausts Windows process handles and crashes the OS.
   `claude_subprocess_session()` wraps a chain-run / launcher entry point
   in a context manager that reaps orphans on entry AND exit.

Use:
    from promptpilot._subprocess import safe_run, claude_subprocess_session

    # Encoding-safe subprocess
    r = safe_run(["powershell", "-Command", "Get-Process"], capture_output=True, text=True)

    # Wrap a launcher loop so any zombies it leaves are reaped automatically
    with claude_subprocess_session("extra_gated_runs"):
        for r in range(args.start, args.start + args.count):
            run_chain_once(...)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import Iterator


def safe_run(*args, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with utf-8 enforced when text mode is implied.

    "Text mode is implied" if any of these is set:
      - text=True
      - universal_newlines=True
      - encoding= (any value)

    In that case, encoding defaults to "utf-8" and errors defaults to "replace"
    if not explicitly passed. Binary mode (text=False) is left alone — caller
    must handle bytes themselves.

    All other args/kwargs pass through to subprocess.run.
    """
    text_mode = (
        kwargs.get("text") is True
        or kwargs.get("universal_newlines") is True
        or "encoding" in kwargs
    )
    if text_mode:
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(*args, **kwargs)


def reap_claude_orphans() -> int:
    """Kill claude.exe processes whose parent process no longer exists.

    Orphans accumulate when a harness run crashes or is Ctrl-C'd while a
    claude.exe subprocess is in flight: the child continues running, retries
    API calls indefinitely, and competes for the API key's concurrent-request
    quota. ~10+ orphans can exhaust Windows process handles and crash the OS.

    Windows-only; no-op on other platforms. Returns the number of processes
    killed (best effort).
    """
    if os.name != "nt":
        return 0
    try:
        # List all claude.exe PIDs with their parent PIDs
        proc = safe_run(
            ["wmic", "process", "where", "Name='claude.exe'",
             "get", "ProcessId,ParentProcessId", "/format:csv"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return 0
        claude_pairs: list[tuple[int, int]] = []
        for line in proc.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                ppid = int(parts[1])
                pid = int(parts[2])
                claude_pairs.append((pid, ppid))
            except (ValueError, IndexError):
                continue

        if not claude_pairs:
            return 0

        # List all live PIDs to detect orphans
        live_proc = safe_run(
            ["wmic", "process", "get", "ProcessId", "/format:csv"],
            capture_output=True, text=True, timeout=30,
        )
        if live_proc.returncode != 0:
            return 0
        live_pids: set[int] = set()
        for line in live_proc.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                live_pids.add(int(parts[1]))
            except (ValueError, IndexError):
                continue

        killed = 0
        for pid, ppid in claude_pairs:
            if ppid not in live_pids:
                # Orphan — kill the process tree rooted at this claude.exe
                try:
                    safe_run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, timeout=10,
                    )
                    killed += 1
                except Exception:
                    pass
        return killed
    except Exception:
        return 0


@contextmanager
def claude_subprocess_session(label: str = "") -> Iterator[None]:
    """Wrap a sequence of claude.exe subprocess invocations with auto-reap.

    Reaps claude.exe orphans on entry (cleans up from any prior crashed run)
    AND on exit (catches anything we may have leaked, even if the body raised).
    Use for launcher entry points (e.g. extra_*_runs.py main, run_chain_once)
    so future launcher scripts get the protection automatically.

    Args:
        label: optional tag printed when zombies are reaped on exit. Helps
            identify which run was leaking.

    Example:
        with claude_subprocess_session("extra_gated_runs"):
            for r in range(args.start, args.start + args.count):
                run_chain_once(chain, "claude-code", "gated_session", r, out_dir)
    """
    t0 = time.time()
    pre_reaped = reap_claude_orphans()
    if pre_reaped > 0:
        sys.stderr.write(
            "[claude_subprocess_session{0}] reaped {1} pre-existing orphans\n"
            .format(":" + label if label else "", pre_reaped)
        )
    try:
        yield
    finally:
        post_reaped = reap_claude_orphans()
        if post_reaped > 0:
            elapsed = time.time() - t0
            sys.stderr.write(
                "[claude_subprocess_session{0}] reaped {1} on exit ({2:.0f}s)\n"
                .format(":" + label if label else "", post_reaped, elapsed)
            )
