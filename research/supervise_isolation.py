r"""Supervisor for the claude session-isolation run: relaunch-on-death (L5).

Three harness deaths on 2026-06-11 had three different external causes (reaper tree-kill,
console CTRL_CLOSE, job-object teardown). The launch chain now removes all three, but the
honest position is that unknown killers may remain — so this loop makes any death self-heal:
run the (resume-aware) runner; if it exits without finishing, relaunch it, up to MAX_RESTARTS.

Exit-code contract with session_isolation_experiment.py:
  0 = all arm-runs saved (DONE)        2 = QuotaExhausted (STOP — needs a window reset,
  3 = finished pass with failures      restarting now would burn quota for nothing)
  anything else = abnormal death       (3 / abnormal -> relaunch; resume skips saved runs)

Writes its own explicitly-flushed supervisor.log so the restart history survives any crash.
Run me detached + console-less (via detach_run.py) from a Task-Scheduler .cmd so the whole
subtree is outside Claude Code's job objects AND has no console.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("PROMPTPILOT_OUT_DIR",
                          r"B:\LLM\_session_retest_2026-06-07\claude_isolation"))
MAX_RESTARTS = int(os.environ.get("PROMPTPILOT_SUPERVISE_RESTARTS", "5"))
RESTART_DELAY_S = 30

LOG = open(OUT / "supervisor.log", "a", encoding="utf-8")


def say(msg: str) -> None:
    line = "[supervisor {0}] {1}".format(time.strftime("%H:%M:%S"), msg)
    LOG.write(line + "\n")
    LOG.flush()
    print(line, flush=True)


def main() -> int:
    runner = [sys.executable, "-u", "research/session_isolation_experiment.py",
              "--runs", "5", "--tool", "claude-code", "--normalizer", "slm-openai"]
    say("starting (max restarts: {0}; runner: {1})".format(MAX_RESTARTS, " ".join(runner[1:])))
    for attempt in range(1, MAX_RESTARTS + 2):
        say("attempt {0}: launching runner".format(attempt))
        t0 = time.time()
        rc = subprocess.call(runner, cwd=str(ROOT))
        dt = (time.time() - t0) / 60
        if rc == 0:
            say("runner finished COMPLETE after {0:.1f} min — done.".format(dt))
            return 0
        if rc == 2:
            say("runner hit the QUOTA wall after {0:.1f} min — stopping (re-run me after "
                "the window resets; resume continues from saved runs).".format(dt))
            return 2
        say("runner died rc={0} after {1:.1f} min (3=pass-with-failures, other=abnormal)"
            .format(rc, dt))
        if attempt > MAX_RESTARTS:
            say("max restarts exhausted — giving up. Inspect runner.log / supervisor.log.")
            return 1
        say("relaunching in {0}s (resume-aware: saved runs are skipped)".format(RESTART_DELAY_S))
        time.sleep(RESTART_DELAY_S)
    return 1


if __name__ == "__main__":
    sys.exit(main())
