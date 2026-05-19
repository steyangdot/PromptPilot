#!/usr/bin/env python3
"""One-shot promptpilot setup (repo-clone install path).

Run from the repo root:
    python quickstart.py

PyPI users should run `prpt setup` instead, which delegates to the same module
(`prpt.setup`). Both paths share check/install/auth/smoke logic so behavior
stays consistent.

This script exists only because pip-installation is bootstrap-style: you need
the package on disk to run `prpt setup`, and a brand-new clone has not been
installed yet. The script handles the editable install, then the rest of the
flow is identical to `prpt setup`.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _main() -> int:
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from prpt.setup import run_setup
    return run_setup(mode="setup")


if __name__ == "__main__":
    sys.exit(_main())
