r"""Launch the claude session-isolation harness FULLY DETACHED on Windows.

Why: a Task-Scheduler job in the interactive session still receives CTRL_CLOSE when the
Claude Code app/console closes (killed our run with STATUS_CONTROL_C_EXIT). A process
created with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW has NO console,
so console-close events can't reach it; it also survives its launcher exiting, and (with the
fixed reaper) is never reaped because it's python.exe, not `claude -p`. It dies only on
logoff/shutdown — not when the app closes.

Run once (it spawns the harness and exits):
    python research/detach_run.py
Resume-aware: completed runs are loaded, so this continues where a prior run died.
Monitor: B:\LLM\_session_retest_2026-06-07\claude_isolation\runner.log
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the gate-measure worktree
OUT = r"B:\LLM\_session_retest_2026-06-07\claude_isolation"
os.makedirs(OUT, exist_ok=True)

if os.name != "nt":
    sys.exit("detach_run.py is Windows-only (uses DETACHED_PROCESS creation flags)")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

env = dict(os.environ,
           PROMPTPILOT_OUT_DIR=OUT,
           CLAUDE_MODEL="claude-opus-4-8",
           CLAUDE_TIMEOUT_SEC="1800",
           # PYTHONIOENCODING=utf-8 makes sys.stdout already-utf8, so chain_test_v2's
           # import-time TextIOWrapper rewrap (line ~106) — which silently DISCARDED -u and
           # block-buffered all progress (lost on every hard kill) — never triggers.
           PYTHONIOENCODING="utf-8",
           PYTHONUNBUFFERED="1")

log = open(os.path.join(OUT, "runner.log"), "a", encoding="utf-8", buffering=1)
log.write("\n[detach_run] launching DETACHED supervisor (no console; restarts the runner on death)\n")
log.flush()

# Spawn the SUPERVISOR (which loops the resume-aware runner) rather than the runner directly:
# any single death self-heals. NOTE (measured 2026-06-11): these flags remove the CONSOLE but
# do NOT escape Claude-Code job objects — run me from a Task-Scheduler .cmd (outside the app's
# job/tree) for true isolation; from inside the app this is still job-killable.
proc = subprocess.Popen(
    [sys.executable, "-u", "research/supervise_isolation.py"],
    cwd=str(ROOT), env=env,
    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    close_fds=True,
)
print("detached supervisor pid:", proc.pid)
print("monitor:", os.path.join(OUT, "runner.log"), "and supervisor.log")
