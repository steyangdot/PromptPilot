"""Standalone diagnostic for the 'T1 bails with zero tokens' mystery.

Reproduces the T1 prompt of chain1 exactly as the harness prepares it, then
launches claude-code as a subprocess with FULL logging:

- stdout -> diag_t1_<ts>/stdout.json
- stderr -> diag_t1_<ts>/stderr.log (not DEVNULL, so we see anything claude-code complains about)
- every 30 s: log elapsed time, stdout size, stderr size, node.exe network conn count
- kills at 10 min hard ceiling, or captures normal completion

Run this with the harness NOT running (to avoid API contention). Then inspect
the log dir to see whether claude-code actually started talking to the API or
was blocked at startup.

    python diagnose_t1.py

Requires the same env as the harness (ANTHROPIC_API_KEY set).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Reuse the harness's own prompt-preparation code so we're testing the real T1
# path (SLM rewrite + scope-gated context loading).
sys.path.insert(0, str(Path(__file__).parent))
import chain_test_v2 as h  # noqa: E402
import agentic_variety_test as av  # noqa: E402


CHAIN1_T1_RAW = "fix the timeout not being passed through to the underlying socket in the sync client"
TIMEOUT_SEC = 600
POLL_SEC = 30
CLAUDE_CMD = [
    "cmd", "/c",
    str(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.CMD"),
    "-p",
    "--output-format", "json",
    "--dangerously-skip-permissions",
    "--model", "sonnet",
    "--no-session-persistence",
    "--bare",
]


def netstat_for_pid(pid: int) -> int:
    """Return count of established TCP connections for pid (best-effort)."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
            errors="replace",
        ).stdout
        count = 0
        for line in out.splitlines():
            if " ESTABLISHED " in line and line.strip().endswith(str(pid)):
                count += 1
        return count
    except Exception:
        return -1


def find_node_pid(parent_pid: int) -> int | None:
    """Walk children of parent_pid looking for node.exe. Returns first match."""
    try:
        out = subprocess.run(
            ["wmic", "process", "get", "Name,ProcessId,ParentProcessId", "/format:csv"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
            errors="replace",
        ).stdout
        # Build ppid->children map, then BFS
        children: dict[int, list[tuple[int, str]]] = {}
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4 and parts[1] and parts[2].isdigit() and parts[3].isdigit():
                name, ppid, pid = parts[1], int(parts[2]), int(parts[3])
                children.setdefault(ppid, []).append((pid, name))
        visit = [parent_pid]
        seen: set[int] = set()
        while visit:
            cur = visit.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for pid, name in children.get(cur, []):
                if name.lower() == "node.exe":
                    return pid
                visit.append(pid)
    except Exception:
        return None
    return None


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent / f"diag_t1_{ts}"
    log_dir.mkdir(exist_ok=True)
    print(f"Log dir: {log_dir}")

    # Reset repo + clear session so we're reproducing fresh T1 state
    h.clear_session(h.HTTPX_DIR)
    h.reset_repo(h.HTTPX_DIR)

    # Prepare the same prompt the harness would send
    prepared = h.prepare_no_session(CHAIN1_T1_RAW, h.HTTPX_DIR, "claude-code")
    prompt = prepared["optimized"]
    (log_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (log_dir / "prepared.meta").write_text(
        f"intent={prepared['intent']}\nscope={prepared['scope']}\n"
        f"prompt_chars={len(prompt)}\nhad_history={prepared['had_history']}\n",
        encoding="utf-8",
    )
    print(f"Prompt prepared: {len(prompt)} chars, intent={prepared['intent']}, scope={prepared['scope']}")

    stdout_path = log_dir / "stdout.json"
    stderr_path = log_dir / "stderr.log"
    timeline_path = log_dir / "timeline.log"

    timeline = open(timeline_path, "w", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line)
        timeline.write(line + "\n")
        timeline.flush()

    log(f"launching: {' '.join(CLAUDE_CMD)}")
    t0 = time.time()

    with open(stdout_path, "wb") as fout, open(stderr_path, "wb") as ferr:
        proc = subprocess.Popen(
            CLAUDE_CMD,
            stdin=subprocess.PIPE, stdout=fout, stderr=ferr,
            cwd=h.HTTPX_DIR,
        )
        # Write prompt to stdin and close
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
            log(f"stdin written ({len(prompt)} bytes) and closed, pid={proc.pid}")
        except Exception as e:
            log(f"stdin write FAILED: {e!r}")

        next_poll = t0 + POLL_SEC
        node_pid: int | None = None
        while True:
            rc = proc.poll()
            now = time.time()
            elapsed = now - t0
            if rc is not None:
                log(f"process exited rc={rc} after {elapsed:.1f}s")
                break
            if elapsed >= TIMEOUT_SEC:
                log(f"HARD TIMEOUT at {elapsed:.1f}s -- killing tree")
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
                rc = -1
                break
            if now >= next_poll:
                if node_pid is None:
                    node_pid = find_node_pid(proc.pid)
                    if node_pid:
                        log(f"discovered node.exe pid={node_pid}")
                conn = netstat_for_pid(node_pid) if node_pid else -1
                stdout_sz = stdout_path.stat().st_size
                stderr_sz = stderr_path.stat().st_size
                log(f"+{elapsed:5.0f}s  stdout={stdout_sz}B  stderr={stderr_sz}B  node_conn={conn}")
                next_poll = now + POLL_SEC
            time.sleep(1)

    timeline.close()

    print()
    print("=== SUMMARY ===")
    print(f"rc: {rc}")
    print(f"elapsed: {time.time()-t0:.1f}s")
    print(f"stdout: {stdout_path.stat().st_size} bytes")
    print(f"stderr: {stderr_path.stat().st_size} bytes")
    if stderr_path.stat().st_size > 0:
        print("--- stderr (first 2000 chars) ---")
        print(stderr_path.read_text(encoding="utf-8", errors="replace")[:2000])


if __name__ == "__main__":
    main()
