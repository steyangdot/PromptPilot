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
    from prpt._subprocess import safe_run, claude_subprocess_session

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


def _is_harness_agent_cmdline(cmdline: str) -> bool:
    """True ONLY for the harness's non-interactive `claude -p` agents — never the user's
    interactive Claude Code app. `-p`/`--print` is the defining discriminator: the harness
    runs `claude -p --output-format json ...`; the interactive app never uses `-p`.
    Fail-safe: an empty/unknown command line returns False (never killed).
    """
    if not cmdline:
        return False
    cl = " " + cmdline.lower().replace("\t", " ") + " "
    return " -p " in cl or " --print " in cl


def reap_claude_orphans() -> int:
    """Kill ORPHANED harness `claude -p` agents only — never the interactive Claude Code app.

    Orphans accumulate when a harness run crashes/Ctrl-C's while a claude.exe child is in
    flight: it keeps retrying API calls, competes for quota, and ~10+ can exhaust Windows
    process handles.

    SAFETY (rewritten 2026-06-11 after this killed a live run AND risked crashing the app):
    the previous version killed ANY claude.exe whose parent had exited, via `taskkill /F /T`
    (whole process TREE). But the user's Claude Code *application* is also claude.exe, and a
    background harness launched from it runs INSIDE that app's process tree — so the tree-kill
    cascaded into the running experiment and would crash the app. This version
      (a) kills ONLY processes whose command line is a `claude -p` agent (the app is
          interactive, never -p) — see _is_harness_agent_cmdline;
      (b) drops `/T`, so a kill can never cascade into a tree;
      (c) is a no-op when PROMPTPILOT_REAP_ORPHANS=0.
    Windows-only. Returns the number killed (best effort).
    """
    if os.name != "nt" or os.environ.get("PROMPTPILOT_REAP_ORPHANS", "1") == "0":
        return 0
    try:
        # /format:list (not csv): command lines contain commas, which corrupt CSV columns.
        proc = safe_run(
            ["wmic", "process", "where", "Name='claude.exe'",
             "get", "CommandLine,ParentProcessId,ProcessId", "/format:list"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return 0
        # Parse blank-line-separated key=value records.
        records: list[dict] = []
        cur: dict = {}
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line:
                if cur:
                    records.append(cur)
                    cur = {}
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
        if cur:
            records.append(cur)
        if not records:
            return 0

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
        for rec in records:
            try:
                pid = int(rec.get("ProcessId", ""))
                ppid = int(rec.get("ParentProcessId", ""))
            except (ValueError, TypeError):
                continue
            if ppid in live_pids:
                continue  # not an orphan
            if not _is_harness_agent_cmdline(rec.get("CommandLine", "")):
                continue  # interactive app / unknown command line -> NEVER kill
            try:
                # NO /T: kill only this orphan agent; it cannot cascade into a tree.
                safe_run(["taskkill", "/F", "/PID", str(pid)],
                         capture_output=True, timeout=10)
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
