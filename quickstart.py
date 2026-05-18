#!/usr/bin/env python3
"""One-shot promptpilot setup: prerequisite checks, install, auth probe, verification.

Run from the repo root:
    python quickstart.py

Idempotent -- safe to re-run. Reports what's already set up vs what needs
manual action. Does NOT auto-enter credentials or open OAuth flows; those
require human input. For interactive auth, this script prints the exact
commands you should run next.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent

# ANSI color helpers (no-op on Windows old terminals; modern Windows handles them fine)
_RED   = "\033[31m"
_GRN   = "\033[32m"
_YEL   = "\033[33m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"
_RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {_GRN}OK{_RESET}    {msg}")


def warn(msg: str) -> None:
    print(f"  {_YEL}WARN{_RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}FAIL{_RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {_DIM}info{_RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{_BOLD}{title}{_RESET}")


def run(cmd: list[str], **kwargs) -> tuple[int, str]:
    """Run a command, return (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, **kwargs,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_python() -> bool:
    section("1. Python")
    v = sys.version_info
    if (v.major, v.minor) < (3, 9):
        fail(f"Python {v.major}.{v.minor} -- promptpilot requires >= 3.9")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
    return True


def check_coding_cli() -> dict:
    """Probe both claude and codex CLIs. At least one must be present.

    Returns dict with keys ``claude_present``, ``codex_present``,
    ``claude_version``, ``codex_version``.
    """
    section("2. Coding agent CLI (need at least one of claude / codex)")
    state = {"claude_present": False, "codex_present": False,
             "claude_version": "", "codex_version": ""}

    # Probe claude (Anthropic). On Windows it's claude.cmd.
    claude_path = shutil.which("claude") or shutil.which("claude.cmd")
    if claude_path:
        rc, out = run([claude_path, "--version"])
        if rc == 0:
            state["claude_present"] = True
            state["claude_version"] = out.strip().splitlines()[0]
            ok(f"claude CLI: {state['claude_version']}")
        else:
            warn(f"claude found but `--version` failed: {out.strip()[:60]}")
    else:
        info("claude CLI not in PATH (skip if you only use codex)")

    # Probe codex (OpenAI).
    codex_path = shutil.which("codex") or shutil.which("codex.cmd")
    if codex_path:
        rc, out = run([codex_path, "--version"])
        if rc == 0:
            state["codex_present"] = True
            state["codex_version"] = out.strip().splitlines()[0]
            ok(f"codex CLI: {state['codex_version']}")
        else:
            warn(f"codex found but `--version` failed: {out.strip()[:60]}")
    else:
        info("codex CLI not in PATH (skip if you only use claude)")

    if not (state["claude_present"] or state["codex_present"]):
        fail("Neither claude nor codex installed. Install at least one:")
        info("  claude: https://docs.anthropic.com/en/docs/claude-code")
        info("  codex:  npm install -g @openai/codex (or see openai docs)")
    return state


def _pick_extras(cli_state: dict) -> str:
    """Pick the pyproject extras to install based on which coding CLIs are present.

    - both claude and codex → `all`  (anthropic + openai SDKs + tiktoken)
    - claude only           → `claude`
    - codex only            → `codex`  (openai SDK + tiktoken)
    - neither               → `claude` (best-effort default; caller will already
                              have errored about missing CLI)
    """
    has_claude = bool(cli_state.get("claude_present"))
    has_codex = bool(cli_state.get("codex_present"))
    if has_claude and has_codex:
        return "all"
    if has_codex and not has_claude:
        return "codex"
    return "claude"


def install_promptpilot(cli_state: dict) -> bool:
    extra = _pick_extras(cli_state)
    section(f"3. Install promptpilot (editable, with {extra} extras)")
    # Probe: is promptpilot already importable AND points at this repo?
    rc, out = run([sys.executable, "-c",
                   "import prpt, os; print(os.path.dirname(prpt.__file__))"])
    if rc == 0:
        installed_path = Path(out.strip()).resolve()
        expected = (REPO_ROOT / "promptpilot").resolve()
        if installed_path == expected:
            ok(f"already installed (editable, pointing at {expected})")
            return True
        warn(f"promptpilot importable but points at {installed_path}, not this repo")
        info("Will reinstall in editable mode from this repo.")

    spec = f".[{extra}]"
    info(f"Running: pip install -e {spec}")
    rc, out = run(
        [sys.executable, "-m", "pip", "install", "-e", spec],
        cwd=str(REPO_ROOT),
    )
    if rc != 0:
        fail("pip install failed")
        info(out.strip()[-500:])
        return False
    ok(f"pip install -e {spec} succeeded")
    return True


def check_promptpilot_cli() -> bool:
    section("4. Verify promptpilot CLI")
    if not shutil.which("promptpilot"):
        fail("`promptpilot` not found in PATH after install")
        info("Try: hash -r  (bash) or restart shell, then re-run")
        return False
    rc, out = run(["promptpilot", "--help"])
    if rc != 0:
        fail("`prpt --help` failed")
        return False
    ok("`prpt --help` works")
    return True


def check_auth(cli_state: dict) -> dict:
    """Probe all four auth modes. Reports what's set up; doesn't pick one.

    Returns dict with keys ``max``, ``codex_chatgpt``, ``anthropic_api``,
    ``openai_api`` (all bool), plus ``judge_available`` (bool: whether
    handoff/restart will work with current setup).
    """
    section("5. Authentication (pick at least one)")
    state = {"max": False, "codex_chatgpt": False,
             "anthropic_api": False, "openai_api": False,
             "judge_available": False}

    # Probe Max OAuth (only if claude is present)
    if cli_state["claude_present"]:
        claude_path = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
        rc, out = run([claude_path, "auth", "status"])
        if rc == 0 and ('"loggedIn": true' in out or '"loggedIn":true' in out):
            state["max"] = True
            ok("Max OAuth (claude): logged in")

    # Probe codex login status (only if codex is present)
    if cli_state["codex_present"]:
        codex_path = shutil.which("codex") or shutil.which("codex.cmd") or "codex"
        rc, out = run([codex_path, "login", "status"])
        if rc == 0 and ("Logged in" in out or "logged in" in out):
            state["codex_chatgpt"] = True
            ok(f"codex login: {out.strip().splitlines()[0]}")

    # Load .env (quote pairs, smart quotes, comments, shell-shadow detection)
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from prpt.core.dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except Exception as e:
        warn(f"could not load .env: {e}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        state["anthropic_api"] = True
        ok("ANTHROPIC_API_KEY set (.env or shell)")
    if os.environ.get("OPENAI_API_KEY"):
        state["openai_api"] = True
        ok("OPENAI_API_KEY set (.env or shell)")

    # The handoff/restart judge can use ONE of: Max OAuth, Codex CLI
    # subscription, anthropic API key, or OpenAI API key. Auto-detect order
    # (judges/judge.py): max > codex > anthropic > openai.
    state["judge_available"] = (
        state["max"] or state["codex_chatgpt"]
        or state["anthropic_api"] or state["openai_api"]
    )

    if not any(state[k] for k in ("max", "codex_chatgpt", "anthropic_api", "openai_api")):
        fail("No auth detected.")
        info("Pick at least one. For coding agents:")
        info("  - claude:  claude auth login --claudeai   (Max subscription, recommended)")
        info("  - codex:   codex login                    (ChatGPT subscription)")
        info("  - either:  put ANTHROPIC_API_KEY=... or OPENAI_API_KEY=... in .env")
    elif not state["max"] and not state["codex_chatgpt"] and state["anthropic_api"]:
        info("Tip: `claude auth login --claudeai` enables Max OAuth — no API charges for")
        info("the SLM normalizer/judge if your subscription covers the workload.")

    return state


def smoke_test_judge(auth_state: dict) -> bool:
    section("6. Smoke test (one judge call -- the handoff/restart path)")
    if not auth_state["judge_available"]:
        warn("Skipping -- no judge-compatible auth available")
        info("(codex ChatGPT subscription alone doesn't power the judge; "
             "set OPENAI_API_KEY or ANTHROPIC_API_KEY, or use Max OAuth)")
        return False
    code = (
        "import sys; sys.path.insert(0, r'" + str(REPO_ROOT) + "');"
        "from prpt.core.dotenv import load_dotenv; "
        "from pathlib import Path; "
        "load_dotenv(Path(r'" + str(REPO_ROOT) + "') / '.env');"
        "from prpt.judges import get_default_judge;"
        "j = get_default_judge();"
        "text, cost, walltime = j('Reply with the single word OK and nothing else.', timeout=30);"
        "print(f'judge={j.name} text={text.strip()!r} cost=${cost:.5f} walltime={walltime:.2f}s')"
    )
    rc, out = run([sys.executable, "-c", code])
    if rc != 0 or not out.strip():
        fail(f"Smoke test failed: {out.strip()[-200:]}")
        return False
    print(f"  {_DIM}{out.strip()}{_RESET}")
    if "OK" in out or "Ok" in out:
        ok("Round-trip works")
        return True
    warn(f"Got non-OK response, but call succeeded -- judge is wired correctly")
    return True


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"{_BOLD}promptpilot quickstart{_RESET}")
    print(f"{_DIM}Repo root: {REPO_ROOT}{_RESET}")

    failures = 0
    if not check_python():
        return 1

    cli_state = check_coding_cli()
    if not (cli_state["claude_present"] or cli_state["codex_present"]):
        failures += 1

    if not install_promptpilot(cli_state):
        failures += 1
    if not check_promptpilot_cli():
        failures += 1

    auth_state = check_auth(cli_state)
    has_any_auth = any(auth_state[k] for k in ("max", "codex_chatgpt",
                                                "anthropic_api", "openai_api"))
    if not has_any_auth:
        failures += 1

    if auth_state["judge_available"]:
        smoke_test_judge(auth_state)

    section("Next steps")
    if failures == 0 and has_any_auth:
        print(f"  {_GRN}Setup complete.{_RESET} You can use:")
        if cli_state["claude_present"] and (auth_state["max"] or auth_state["anthropic_api"]):
            print(f"      prpt --tool claude-code \"fix the flaky payment test\"")
        if cli_state["codex_present"] and (auth_state["codex_chatgpt"] or auth_state["openai_api"]):
            print(f"      prpt --tool codex \"fix the flaky payment test\"")
        if auth_state["judge_available"]:
            print(f"  When the session feels heavy:")
            print(f"      prpt restart   # snapshots to ./handoff.md and starts fresh")
        print(f"  See QUICKSTART.md for the full guide.")
        return 0
    print(f"  {_YEL}{failures} step(s) need attention.{_RESET} Fix the items above and re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
