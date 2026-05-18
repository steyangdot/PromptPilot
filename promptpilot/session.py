"""Session transcript — persists conversation turns across CLI invocations."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

SESSION_TTL = 7200   # seconds before a session is considered stale (2 hours)
MAX_TURNS   = 4      # how many prior user+assistant pairs to surface
                     # chain1 validation (2026-04-21) showed 5-turn referential
                     # chains lose context at T4/T5 when MAX_TURNS=2 truncates
                     # the window to the last 2 pairs — bumped to 4 so all prior
                     # turns of a typical multi-turn session stay in view


def _session_path(cwd: str) -> Path:
    key = hashlib.sha256(os.path.abspath(cwd).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "promptpilot_session_{0}.jsonl".format(key)


ORGANIC_TURN_TRUNC = 300   # chars; truncation cap for normal user/assistant turns


def load_recent_turns(cwd: str) -> list[str]:
    """Return the last MAX_TURNS pairs as formatted strings for SLM prepend.

    Each entry is 'USER: ...' or 'ASSISTANT: ...'. Organic turns are truncated
    to ORGANIC_TURN_TRUNC chars; turns marked ``synth: True`` (written by
    bootstrap from a handoff.md) are returned untruncated -- the whole point
    of bootstrap is to carry the user's curated state forward, and truncating
    would defeat the compression intent. Returns [] when there is no session
    or the session has expired.
    """
    path = _session_path(cwd)
    if not path.exists():
        return []
    try:
        now = time.time()
        messages: list[tuple[str, str, bool]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if now - obj.get("ts", 0) > SESSION_TTL:
                continue  # skip stale entries
            role    = obj.get("role", "")
            content = obj.get("content", "")
            synth   = bool(obj.get("synth", False))
            if role in ("user", "assistant") and content:
                messages.append((role, content, synth))

        recent = messages[-(MAX_TURNS * 2):]
        return [
            "{0}: {1}".format(r.upper(), c if synth else c[:ORGANIC_TURN_TRUNC])
            for r, c, synth in recent
        ]
    except Exception:
        return []


def load_all_turns(cwd: str) -> list[dict]:
    """Return every turn from the session JSONL, untruncated, without TTL filter.

    Used by the handoff/checkpoint flow which wants the full transcript for
    Haiku to summarize -- not the bounded MAX_TURNS window or the TTL-filtered
    view that load_recent_turns produces. Returns a list of dicts with keys
    ``role``, ``content``, ``ts`` (or [] if no session file exists).
    """
    path = _session_path(cwd)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("role") in ("user", "assistant") and obj.get("content"):
                out.append({
                    "role": obj["role"],
                    "content": obj["content"],
                    "ts": float(obj.get("ts", 0)),
                })
    except Exception:
        return []
    return out


def append_turn(cwd: str, role: str, content: str) -> None:
    """Append a single turn to the session transcript (organic, truncatable)."""
    path = _session_path(cwd)
    try:
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "content": content, "ts": time.time()}) + "\n")
    except Exception:
        pass


def append_synth_turn(cwd: str, role: str, content: str) -> None:
    """Append a synthesized turn (e.g. from bootstrap of a handoff.md).

    Synth turns are flagged so ``load_recent_turns`` can skip truncation:
    they're a single turn carrying compressed prior state, so truncating
    them would lose most of the value.
    """
    path = _session_path(cwd)
    try:
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "role": role, "content": content, "ts": time.time(),
                "synth": True,
            }) + "\n")
    except Exception:
        pass


def clear_session(cwd: str) -> None:
    """Delete the session transcript for this working directory."""
    try:
        _session_path(cwd).unlink(missing_ok=True)
    except Exception:
        pass


def session_path_for(cwd: str) -> str:
    """Return the session file path as a string (for display purposes)."""
    return str(_session_path(cwd))
