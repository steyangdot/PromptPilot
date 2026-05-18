"""Minimal .env loader with shell-shadow detection.

Lives in `promptpilot.core` so quickstart.py and research scripts can both
use it without quickstart having to import from research code.

Usage:
    from promptpilot.core.dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).parent / ".env")
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from env_path into os.environ.

    - Skips comments (lines starting with #) and blank lines.
    - Strips matched surrounding quotes (ASCII or curly).
    - Does NOT override existing os.environ values (shell wins).
    - Warns to stderr when the shell shadows a different .env value, so
      the user knows their .env edit is being silently ignored.

    No-ops silently if env_path doesn't exist.
    """
    if not env_path.exists():
        return
    shadowed: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip a matched pair of surrounding quotes (ASCII or smart/curly)
        _quote_pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))
        for lq, rq in _quote_pairs:
            if len(val) >= 2 and val[0] == lq and val[-1] == rq:
                val = val[1:-1]
                break
        if not key:
            continue
        existing = os.environ.get(key)
        if existing and existing != val:
            shadowed.append(key)
        if not existing:
            os.environ[key] = val
    if shadowed:
        sys.stderr.write(
            "[dotenv] WARNING: shell environment shadows .env value for: "
            "{0}\n  Your .env value is being IGNORED. To use .env "
            "exclusively (recommended -- keeps keys off-screen):\n"
            "    Windows PowerShell:  Remove-Item Env:{1}\n"
            "    Windows cmd:         set {1}=\n"
            "    bash/zsh:            unset {1}\n"
            "  Then restart this process.\n".format(
                ", ".join(shadowed), shadowed[0])
        )
