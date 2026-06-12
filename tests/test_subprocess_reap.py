"""Safety tests for the claude-orphan reaper's command-line discriminator.

The reaper killed a live experiment and risked crashing the user's Claude Code app because
it couldn't tell its own `claude -p` agents from the interactive app (both are claude.exe).
_is_harness_agent_cmdline is the guard: it must return True ONLY for `-p`/`--print` agents,
and NEVER for an interactive `claude` (the app) or an unknown/empty command line.
"""
from prpt._subprocess import _is_harness_agent_cmdline


def test_harness_agents_are_killable():
    assert _is_harness_agent_cmdline("claude -p --output-format json --model claude-opus-4-8")
    assert _is_harness_agent_cmdline(r"C:\Program Files\claude\claude.exe -p --output-format json")
    assert _is_harness_agent_cmdline("claude --print --output-format json")


def test_interactive_app_is_never_killable():
    # the user's Claude Code app: interactive, never -p
    assert not _is_harness_agent_cmdline("claude")
    assert not _is_harness_agent_cmdline(r"C:\Users\me\AppData\Local\claude\claude.exe")
    assert not _is_harness_agent_cmdline("claude --resume 1234")
    assert not _is_harness_agent_cmdline("claude mcp serve")
    # a path containing 'p' or '-p' as a substring must not be mistaken for the -p flag
    assert not _is_harness_agent_cmdline(r"C:\my-project\claude.exe")
    assert not _is_harness_agent_cmdline(r"C:\app-package\claude.exe --output-format text")


def test_unknown_or_empty_is_failsafe():
    assert not _is_harness_agent_cmdline("")
    assert not _is_harness_agent_cmdline(None)  # type: ignore[arg-type]
