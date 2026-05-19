"""Reusable setup/doctor implementation backing both `prpt setup` / `prpt doctor`
and the repo-root `quickstart.py` script.

The repo-cloning install path runs `python quickstart.py` from the repo root,
which calls into this module. PyPI users have no `quickstart.py`; they run
`prpt setup` (one-time onboarding) or `prpt doctor` (checks only) instead.

`run_setup(mode)` returns an exit code: 0 on success, 1 if anything needs
attention. ``mode="setup"`` installs missing dependencies; ``mode="doctor"``
only reports state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ANSI color helpers. Modern Windows terminals (Win10+) handle these fine.
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


def _run(cmd: list, **kwargs) -> tuple:
    """Run a command, return (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, **kwargs,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _repo_root() -> Optional[Path]:
    """Return the repo root if this module was loaded from an editable install,
    else None (e.g. pip install from PyPI)."""
    here = Path(__file__).resolve().parent
    # `prpt/setup.py` -> `prpt/` -> repo root has pyproject.toml
    candidate = here.parent
    if (candidate / "pyproject.toml").exists() and (candidate / "prpt").is_dir():
        return candidate
    return None


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
    """Probe both claude and codex CLIs. At least one must be present."""
    section("2. Coding agent CLI (need at least one of claude / codex)")
    state = {"claude_present": False, "codex_present": False,
             "claude_version": "", "codex_version": ""}

    claude_path = shutil.which("claude") or shutil.which("claude.cmd")
    if claude_path:
        rc, out = _run([claude_path, "--version"])
        if rc == 0:
            state["claude_present"] = True
            state["claude_version"] = out.strip().splitlines()[0]
            ok(f"claude CLI: {state['claude_version']}")
        else:
            warn(f"claude found but `--version` failed: {out.strip()[:60]}")
    else:
        info("claude CLI not in PATH (skip if you only use codex)")

    codex_path = shutil.which("codex") or shutil.which("codex.cmd")
    if codex_path:
        rc, out = _run([codex_path, "--version"])
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
        info("  claude: npm install -g @anthropic-ai/claude-code   (or https://docs.anthropic.com/en/docs/claude-code)")
        info("  codex:  npm install -g @openai/codex                (or https://platform.openai.com/docs)")
    return state


def _pick_extras(cli_state: dict) -> str:
    has_claude = bool(cli_state.get("claude_present"))
    has_codex = bool(cli_state.get("codex_present"))
    if has_claude and has_codex:
        return "all"
    if has_codex and not has_claude:
        return "codex"
    return "claude"


def install_promptpilot(cli_state: dict, mode: str) -> bool:
    """Editable install only when the repo is available locally; otherwise
    rely on whatever pip-installed package is already importable. In doctor
    mode we never install, only report state."""
    repo = _repo_root()
    extra = _pick_extras(cli_state)
    section(f"3. Install prpt (with {extra} extras)")

    # Probe: is prpt already importable?
    rc, out = _run([sys.executable, "-c",
                    "import prpt, os; print(os.path.dirname(prpt.__file__))"])
    if rc == 0:
        installed_path = Path(out.strip()).resolve()
        if repo is not None:
            expected = (repo / "prpt").resolve()
            if installed_path == expected:
                ok(f"already installed (editable, pointing at {expected})")
                return True
            warn(f"prpt importable but points at {installed_path}, not this repo")
            if mode == "doctor":
                info("Will not reinstall in doctor mode. Run `prpt setup` to fix.")
                return True
            info("Will reinstall in editable mode from this repo.")
        else:
            # Not running from a clone — site-packages install is fine.
            ok(f"already installed at {installed_path}")
            return True

    if mode == "doctor":
        fail("prpt not importable")
        info("Run `pip install prpt[claude]` (or `[codex]`/`[all]`)")
        return False

    if repo is None:
        fail("prpt not installed and no local repo to install from")
        info("Run `pip install prpt[claude]` (or `[codex]`/`[all]`)")
        return False

    spec = f".[{extra}]"
    info(f"Running: pip install -e {spec}")
    rc, out = _run(
        [sys.executable, "-m", "pip", "install", "-e", spec],
        cwd=str(repo),
    )
    if rc != 0:
        fail("pip install failed")
        info(out.strip()[-500:])
        return False
    ok(f"pip install -e {spec} succeeded")
    return True


def check_promptpilot_cli() -> bool:
    section("4. Verify prpt CLI")
    if not shutil.which("prpt"):
        fail("`prpt` not found in PATH after install")
        info("Try: hash -r (bash) / `where prpt` (PowerShell), or restart your shell")
        return False
    rc, out = _run(["prpt", "--help"])
    if rc != 0:
        fail("`prpt --help` failed")
        return False
    ok("`prpt --help` works")
    return True


def check_auth(cli_state: dict) -> dict:
    """Probe all four auth modes. Reports state; doesn't pick one."""
    section("5. Authentication (pick at least one)")
    state = {"max": False, "codex_chatgpt": False,
             "anthropic_api": False, "openai_api": False,
             "judge_available": False}

    if cli_state["claude_present"]:
        claude_path = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
        rc, out = _run([claude_path, "auth", "status"])
        if rc == 0 and ('"loggedIn": true' in out or '"loggedIn":true' in out):
            state["max"] = True
            ok("Max OAuth (claude): logged in")

    if cli_state["codex_present"]:
        codex_path = shutil.which("codex") or shutil.which("codex.cmd") or "codex"
        rc, out = _run([codex_path, "login", "status"])
        if rc == 0 and ("Logged in" in out or "logged in" in out):
            state["codex_chatgpt"] = True
            ok(f"codex login: {out.strip().splitlines()[0]}")

    # Load .env from current working directory (where the user invoked prpt).
    try:
        from prpt.core.dotenv import load_dotenv
        load_dotenv(Path.cwd() / ".env")
    except Exception as e:
        warn(f"could not load .env: {e}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        state["anthropic_api"] = True
        ok("ANTHROPIC_API_KEY set (.env or shell)")
    if os.environ.get("OPENAI_API_KEY"):
        state["openai_api"] = True
        ok("OPENAI_API_KEY set (.env or shell)")

    state["judge_available"] = (
        state["max"] or state["codex_chatgpt"]
        or state["anthropic_api"] or state["openai_api"]
    )

    if not any(state[k] for k in ("max", "codex_chatgpt", "anthropic_api", "openai_api")):
        fail("No auth detected.")
        info("Pick at least one:")
        info("  - claude:  claude auth login --claudeai   (Max subscription, recommended)")
        info("  - codex:   codex login                    (ChatGPT subscription)")
        info("  - either:  put ANTHROPIC_API_KEY=... or OPENAI_API_KEY=... in .env")
    elif not state["max"] and not state["codex_chatgpt"] and state["anthropic_api"]:
        info("Tip: `claude auth login --claudeai` enables Max OAuth - no API charges for")
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
        "from pathlib import Path;"
        "from prpt.core.dotenv import load_dotenv;"
        "load_dotenv(Path.cwd() / '.env');"
        "from prpt.judges import get_default_judge;"
        "j = get_default_judge();"
        "text, cost, walltime = j('Reply with the single word OK and nothing else.', timeout=30);"
        "print(f'judge={j.name} text={text.strip()!r} cost=${cost:.5f} walltime={walltime:.2f}s')"
    )
    rc, out = _run([sys.executable, "-c", code])
    if rc != 0 or not out.strip():
        fail(f"Smoke test failed: {out.strip()[-200:]}")
        return False
    print(f"  {_DIM}{out.strip()}{_RESET}")
    if "OK" in out or "Ok" in out:
        ok("Round-trip works")
        return True
    warn("Got non-OK response, but call succeeded -- judge is wired correctly")
    return True


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_setup(mode: str = "setup") -> int:
    """Run the setup/doctor flow. ``mode`` is "setup" (install + check) or
    "doctor" (check only)."""
    if mode not in ("setup", "doctor"):
        raise ValueError(f"mode must be 'setup' or 'doctor', got {mode!r}")

    print(f"{_BOLD}promptpilot {mode}{_RESET}")
    repo = _repo_root()
    if repo is not None:
        print(f"{_DIM}Repo root: {repo}{_RESET}")
    else:
        print(f"{_DIM}Installed from package (no repo clone detected){_RESET}")

    failures = 0
    if not check_python():
        return 1

    cli_state = check_coding_cli()
    if not (cli_state["claude_present"] or cli_state["codex_present"]):
        failures += 1

    if not install_promptpilot(cli_state, mode):
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
        print(f"  {_GRN}{'Setup complete.' if mode == 'setup' else 'All checks pass.'}{_RESET} You can use:")
        if cli_state["claude_present"] and (auth_state["max"] or auth_state["anthropic_api"]):
            print('      prpt "fix the flaky payment test"')
        elif cli_state["codex_present"] and (auth_state["codex_chatgpt"] or auth_state["openai_api"]):
            print('      prpt "fix the flaky payment test"')
        if auth_state["judge_available"]:
            print("  When the session feels heavy:")
            print("      prpt restart   # snapshots to ./handoff.md and starts fresh")
        print("  See QUICKSTART.md for the full guide.")
        return 0
    print(f"  {_YEL}{failures} step(s) need attention.{_RESET} Fix the items above and re-run.")
    return 1
