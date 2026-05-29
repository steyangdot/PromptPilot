"""Reusable SLM-as-harness primitives.

Exports:
- ``judge_via_max(prompt)`` — single-shot Haiku call routed through the
  claude-code CLI subprocess (Max billing). Useful for any SLM-controller
  pattern: handoff-doc generation, command guarding, scope classification.
  Strips ``ANTHROPIC_API_KEY`` from the subprocess env so the CLI uses the
  OAuth/Max session even when an API key is set in the parent process for
  other uses.
- ``extract_json(text)`` — fail-soft JSON parser for SLM outputs. Tries
  direct parse, fenced ``json`` blocks, and a bare ``{...}`` regex.

Originally extracted from the evaluator+retry experiment (chain5 N=3 ×2,
neutral net delta on chain-mean — see session summary 2026-05-06). The
mechanism was scrapped; this module retains the bits that paid forward
into the handoff.md generator and any future SLM-controller work.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional


JUDGE_TIMEOUT_SEC = int(os.environ.get("EVALUATOR_TIMEOUT_SEC", "90"))


def judge_via_max(prompt: str, timeout: int | None = None) -> tuple[str, float, float]:
    """Send ``prompt`` to Haiku via claude-code CLI; return (text, cost_usd, walltime_s).

    Auth: strips ``ANTHROPIC_API_KEY`` from the subprocess env so the CLI uses
    the OAuth/Max session. Tools disabled (``--tools ""``) since this is a
    pure judgment call -- no filesystem access needed. Failure-tolerant:
    on timeout or non-JSON CLI output, returns ("", 0.0, walltime).
    """
    claude = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
    cmd = [
        claude, "-p",
        "--output-format", "json",
        "--model", "haiku",
        "--tools", "",
        "--no-session-persistence",
    ]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Recursion guard: flag this as a promptpilot-spawned SLM subprocess so the
    # nested `claude` invocation's UserPromptSubmit hook (optimize_prompt.py)
    # skips re-running the SLM rewrite. Without it, the rewrite spawns another
    # `claude -p` that re-fires the hook, recursing until the hook timeout kills
    # it (~17s wasted per call). We deliberately do NOT use `--bare`: it forces
    # ANTHROPIC_API_KEY-only auth and never reads OAuth/keychain, which would
    # break this Max path (judge_via_max strips ANTHROPIC_API_KEY on purpose).
    env["PROMPTPILOT_SLM_SUBPROCESS"] = "1"
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout if timeout is not None else JUDGE_TIMEOUT_SEC,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "", 0.0, time.time() - t0
    walltime = time.time() - t0
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "", 0.0, walltime
    return data.get("result", ""), float(data.get("total_cost_usd", 0.0)), walltime


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Optional[dict]:
    """Fail-soft JSON parser for SLM outputs. Returns None on failure."""
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCED_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _BARE_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None
