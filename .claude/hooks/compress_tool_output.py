#!/usr/bin/env python3
"""
Claude Code PostToolUse hook — tool-output compression.

Intercepts Bash tool responses and compresses them before the LLM sees them.
Targets the dominant token bleed in agentic sessions: pytest output, grep
floods, deep find results, verbose git diffs, linter spew, and installer logs.

Hook payload (stdin, JSON):
    {
        "session_id": "...",
        "transcript_path": "...",
        "cwd": "...",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input":    { "command": "pytest tests/ -v" },
        "tool_response": {
            "output":      "...",
            "interrupted": false,
            "error":       ""       # optional
        }
    }

Hook output (stdout, JSON) to override the tool response seen by the LLM:
    {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": "<compressed output>"
        }
    }

Exits 0 with NO stdout (passthrough) when:
  - PROMPTPILOT_COMPRESS_DISABLE env var is truthy
  - Not a Bash tool call
  - Output is short (≤ 500 chars)
  - Command was interrupted (partial output — don't risk further mangling)
  - Compression savings < MIN_SAVINGS_RATIO (20 %)
  - Any error occurs  ← always fails open

Optional telemetry:
  If PROMPTPILOT_COMPRESS_LOG=1 (default on), each compression is appended to
  ~/.promptpilot/compress_stats.jsonl so `prpt stats` can report savings.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve promptpilot on sys.path  (two dirs up from .claude/hooks/)
# ---------------------------------------------------------------------------
_HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HOOK_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Minimum output length worth attempting compression (chars)
_MIN_OUTPUT_LEN = 500

# Telemetry sink
_STATS_PATH = Path(os.environ.get(
    "PROMPTPILOT_COMPRESS_STATS",
    str(Path.home() / ".promptpilot" / "compress_stats.jsonl"),
))


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def _passthrough() -> None:
    """Exit with no output → tool response is left unchanged."""
    sys.exit(0)


def _override(compressed: str) -> None:
    """Emit the replacement tool response and exit.

    Uses ``updatedToolOutput`` -- the field Claude Code honors to replace tool
    output. Replacing output for non-MCP tools (e.g. Bash) requires claude CLI
    >= v2.1.120; on older versions this hook still logs telemetry but the model
    sees the original, uncompressed output. (The old ``toolResponse`` field was
    never recognized, so compression silently no-op'd regardless of version.)
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": compressed,
        }
    }))
    sys.exit(0)


def _log_stat(record: dict) -> None:
    """Append a single JSONL record to the stats file (best-effort)."""
    if _truthy(os.environ.get("PROMPTPILOT_COMPRESS_LOG_DISABLE")):
        return
    try:
        _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _STATS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # never let telemetry break the hook


def main() -> None:
    # ---- 0. Global kill switch ---------------------------------------------
    if _truthy(os.environ.get("PROMPTPILOT_COMPRESS_DISABLE")):
        _passthrough()

    # ---- 1. Read and parse stdin -------------------------------------------
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        _passthrough()

    # ---- 2. Only handle Bash tool calls ------------------------------------
    tool_name: str = payload.get("tool_name", "")
    if tool_name != "Bash":
        _passthrough()

    tool_input: dict = payload.get("tool_input", {})
    tool_response: dict = payload.get("tool_response", {})

    # ---- 3. Respect interrupted flag — partial output is fragile -----------
    if tool_response.get("interrupted") is True:
        _passthrough()

    command: str = tool_input.get("command", "").strip()
    output: str = (tool_response.get("output") or "").strip()

    if not command or not output or len(output) < _MIN_OUTPUT_LEN:
        _passthrough()

    # ---- 4. Import compressor (fails open if package unavailable) ----------
    try:
        from prpt.compress.tool_output import compress, detect_command_type
    except ImportError:
        _passthrough()

    # ---- 5. Compress --------------------------------------------------------
    try:
        compressed = compress(command, output)
        kind = detect_command_type(command)
    except Exception:
        _passthrough()

    # ---- 6. Only override if we actually saved something -------------------
    if compressed == output:
        _log_stat({
            "ts": time.time(),
            "cmd_kind": kind,
            "original_len": len(output),
            "compressed_len": len(output),
            "savings_pct": 0,
            "applied": False,
        })
        _passthrough()

    # Prepend a one-line note so the LLM knows output was filtered
    original_lines = output.count('\n') + 1
    compressed_lines = compressed.count('\n') + 1
    savings_pct = int(100 * (1 - len(compressed) / max(len(output), 1)))
    header = (
        f"[promptpilot/compress {kind}] {original_lines} → {compressed_lines} lines "
        f"({savings_pct}% reduction)\n"
        "─" * 60 + "\n"
    )

    _log_stat({
        "ts": time.time(),
        "cmd_kind": kind,
        "original_len": len(output),
        "compressed_len": len(compressed),
        "savings_pct": savings_pct,
        "applied": True,
    })

    _override(header + compressed)


if __name__ == "__main__":
    main()
