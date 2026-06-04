"""Regression tests for cross-repo context bleed (process cwd vs --cwd).

Background
----------
When ``prpt --cwd <target>`` is run from *inside* a different repo, the SLM
rewrite must be grounded ONLY in ``<target>`` — never in the directory prpt
happened to be launched from. The bug these tests guard against:

  - The repo-context loader is correctly keyed off ``repo.cwd`` (== args.cwd),
    but the *judge subprocess* that actually runs the SLM (``claude -p`` /
    ``codex exec``) used to run in the PARENT process cwd. The CLI resolves
    project context (CLAUDE.md / AGENTS.md / project memory) from its working
    directory, so a mismatched cwd folded the launch repo's own files into the
    grounded prompt — a cross-repo context-bleed / prompt-injection surface.

These tests pin both halves: (1) the context block is built from the target
repo only, and (2) every judge subprocess runs in the target cwd, never the
process cwd.
"""
from __future__ import annotations

import inspect
import os

import pytest

from prpt.core.types import RepoMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingJudge:
    """Fake Judge that records the cwd each call was routed with."""
    name = "fake"

    def __init__(self, reply: str = "INTENT: act\nSCOPE: pinpoint\n---\nDo the thing"):
        self.reply = reply
        self.calls: list[tuple[str, str | None]] = []  # (prompt, cwd)

    def __call__(self, prompt: str, timeout: int = 90, cwd: str | None = None):
        self.calls.append((prompt, cwd))
        return self.reply, 0.0, 0.01


# ---------------------------------------------------------------------------
# 1. Context block is grounded in the target repo, not the process cwd
# ---------------------------------------------------------------------------

def test_context_block_excludes_process_cwd_files(tmp_path, monkeypatch):
    """Build a context block for an unrelated target repo while the *process*
    cwd is a different repo containing a sentinel file. The sentinel must not
    leak into the block.
    """
    from prpt.repo.collector import RepoContextCollector
    from prpt.repo.loader import RepoContentLoader

    process_dir = tmp_path / "promptpilot_like_repo"
    target_dir = tmp_path / "unrelated_target_repo"
    process_dir.mkdir()
    target_dir.mkdir()

    # Sentinel ONLY in the launch directory — must never appear in a block
    # built for the target repo.
    (process_dir / "process_only_factory.py").write_text(
        "# PROCESS_CWD_SECRET\n"
        "def process_only_marker():\n"
        "    return 'lives in the launch repo, not the target'\n",
        encoding="utf-8",
    )
    # A file in the target repo the prompt references by name.
    (target_dir / "target_widget.py").write_text(
        "def target_only_marker():\n"
        "    return 'lives in the target repo'\n",
        encoding="utf-8",
    )

    # Simulate launching prpt from inside one repo while pointing --cwd at
    # an unrelated clone.
    monkeypatch.chdir(process_dir)

    repo = RepoContextCollector().collect(str(target_dir))
    block = RepoContentLoader().build_context_block(
        "explain target_widget.py and target_only_marker", repo, scope="localized",
    )

    # Target content is present...
    assert "target_widget.py" in block
    assert "target_only_marker" in block
    # ...and the launch-repo sentinel is completely absent.
    assert "PROCESS_CWD_SECRET" not in block
    assert "process_only_factory.py" not in block
    assert "process_only_marker" not in block


# ---------------------------------------------------------------------------
# 2. judge_via_max runs the claude subprocess in the supplied cwd
# ---------------------------------------------------------------------------

def test_judge_via_max_runs_subprocess_in_passed_cwd(monkeypatch):
    import prpt.judges.slm as slm

    captured: dict = {}

    class _FakeProc:
        stdout = b'{"result": "ok", "total_cost_usd": 0.0}'

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(slm.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(slm.subprocess, "run", fake_run)

    text, cost, _walltime = slm.judge_via_max("rewrite this", cwd="X:/target/repo")

    assert text == "ok"
    assert captured["cwd"] == "X:/target/repo"


def test_judge_via_max_defaults_cwd_none_when_unspecified(monkeypatch):
    """Repo-agnostic callers (e.g. handoff judge) keep the inherit-cwd default."""
    import prpt.judges.slm as slm

    captured: dict = {}

    class _FakeProc:
        stdout = b'{"result": "ok", "total_cost_usd": 0.0}'

    def fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd", "MISSING")
        return _FakeProc()

    monkeypatch.setattr(slm.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(slm.subprocess, "run", fake_run)

    slm.judge_via_max("summarize this")
    # cwd kwarg is still passed (as None) → subprocess inherits process cwd.
    assert captured["cwd"] is None


# ---------------------------------------------------------------------------
# 3. Judge implementations forward cwd
# ---------------------------------------------------------------------------

def test_max_judge_forwards_cwd(monkeypatch):
    import prpt.judges.judge as judge_mod

    captured: dict = {}

    def fake_via_max(prompt, timeout=90, cwd=None):
        captured["cwd"] = cwd
        return "ok", 0.0, 0.01

    monkeypatch.setattr(judge_mod, "_judge_via_max", fake_via_max)
    monkeypatch.setattr(judge_mod, "warn_subscription_tos_once", lambda: None)

    text, _, _ = judge_mod.MaxHaikuJudge()("prompt", cwd="X:/target")
    assert text == "ok"
    assert captured["cwd"] == "X:/target"


def test_codex_judge_uses_passed_cwd_not_process_cwd(monkeypatch):
    import prpt.judges.judge as judge_mod

    captured: dict = {}

    class _FakeProc:
        stdout = (
            b'{"type": "item.completed", "item": '
            b'{"type": "agent_message", "text": "ok"}}\n'
            b'{"type": "turn.completed", "usage": '
            b'{"input_tokens": 1, "output_tokens": 1}}\n'
        )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(judge_mod.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(judge_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(judge_mod, "warn_codex_subscription_tos_once", lambda: None)

    target = "X:/target/repo"
    text, _, _ = judge_mod.CodexCliJudge()("prompt", cwd=target)

    assert text == "ok"
    # codex's --cd flag must point at the target, not the process cwd.
    assert "--cd" in captured["cmd"]
    cd_value = captured["cmd"][captured["cmd"].index("--cd") + 1]
    assert cd_value == target
    assert cd_value != os.getcwd()
    # ...and the subprocess itself runs there too.
    assert captured["cwd"] == target


# ---------------------------------------------------------------------------
# 4. SubscriptionSLMNormalizer routes repo.cwd (not process cwd) to the judge
# ---------------------------------------------------------------------------

def test_subscription_normalizer_routes_repo_cwd_to_judge(tmp_path):
    """The end-to-end guard: a normalize() with repo.cwd != process cwd must
    route every judge subprocess at repo.cwd.
    """
    from prpt.normalizers.slm_subscription import SubscriptionSLMNormalizer

    target = str(tmp_path)
    assert target != os.getcwd()  # tmp_path is guaranteed distinct

    judge = _RecordingJudge()
    norm = SubscriptionSLMNormalizer(judge=judge, load_repo_content=False)
    repo = RepoMetadata(
        cwd=target, branch="main", changed_files=[],
        dominant_language="Python", test_framework="pytest",
    )

    norm.normalize("fix the flaky payment test", repo)

    assert judge.calls, "judge was never called"
    for prompt, cwd in judge.calls:
        assert cwd == target, "judge call ran in {0!r}, expected {1!r}".format(cwd, target)


def test_subscription_is_referential_threads_cwd(tmp_path):
    from prpt.normalizers.slm_subscription import SubscriptionSLMNormalizer

    target = str(tmp_path)
    judge = _RecordingJudge(reply="REFERENTIAL: no")
    norm = SubscriptionSLMNormalizer(judge=judge, load_repo_content=False)

    norm.is_referential("add a dark-mode toggle", cwd=target)

    assert judge.calls
    assert judge.calls[-1][1] == target


# ---------------------------------------------------------------------------
# 5. SDK normalizers accept (and ignore) the cwd kwarg for interface parity
# ---------------------------------------------------------------------------

def test_sdk_is_referential_accepts_cwd_kwarg():
    """cli.py always calls is_referential(prompt, cwd=args.cwd). Every SLM
    normalizer that exposes it must accept the kwarg without raising.
    """
    from prpt.normalizers.slm_anthropic import SLMNormalizer
    from prpt.normalizers.slm_openai import OpenAISLMNormalizer

    for cls in (SLMNormalizer, OpenAISLMNormalizer):
        params = inspect.signature(cls.is_referential).parameters
        assert "cwd" in params, "{0}.is_referential is missing cwd".format(cls.__name__)


# ---------------------------------------------------------------------------
# 6. shell.py session path is stable across raw vs resolved cwd
# ---------------------------------------------------------------------------

def test_shell_session_path_normalizes_cwd(tmp_path):
    """save uses the raw args.cwd, load uses the resolved repo.cwd — the
    modified-files continuity file must still match across the two forms.
    """
    from prpt.adapters import shell

    messy = os.path.join(str(tmp_path), "sub", "..")  # what a save caller may pass
    clean = str(tmp_path)                              # what the loader resolves to

    shell.save_session_files(messy, ["a.py", "b.py"])
    try:
        assert shell.load_session_files(clean) == ["a.py", "b.py"]
    finally:
        shell._session_path(clean).unlink(missing_ok=True)
