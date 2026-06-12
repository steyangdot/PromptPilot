"""
Multi-turn chain test v2: measure session-history value with metrics that
actually distinguish efficient completion from failure.

Why v2:
  - Output tokens conflate "efficient" with "failed/gave up". A 0-tool-call
    response with 200 output tokens looks "cheaper" than a 21-tool-call
    response with 3,479 tokens, but the first did nothing useful.
  - claude-code is highly nondeterministic: identical prompts can vary 24x
    on output token count. Single-run chain comparisons are unreliable.

What v2 measures (in priority order):
  1. task_success     — did the expected files get touched / explanation produced?
  2. tool_calls       — exploration cost proxy (lower = better with same success)
  3. input_tokens     — context the agent had to consume (lower = better)
  4. failure_rate     — fraction of turns where tool_calls == 0 (agent bailed)
  5. output_tokens    — kept as secondary, no longer drives the verdict

How:
  - Each turn declares `expected_files` and `expected_action` ("modify" or "explain")
  - File hash snapshots are taken before/after each turn to detect actual changes
  - --runs N (default 3) runs the full chain N times per phase, metrics averaged
  - Per-run results saved to chain_results_v2/<tool>/<chain>/run_<n>.json

Usage:
    python chain_test_v2.py --dry-run --chain 1
    python chain_test_v2.py --chain 1 --tool claude-code --runs 3
    python chain_test_v2.py --tool codex --runs 3
    python chain_test_v2.py --reprint
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


# Make THIS worktree's prpt win over any editable install (`pip install -e` in another
# checkout, whose prpt can lag this branch and lack prpt.verify) — and do it BEFORE the
# first prpt import below, or a stale prpt gets cached in sys.modules and the harness
# crashes when run from a worktree (ModuleNotFoundError: prpt.verify).
sys.path.insert(0, str(Path(__file__).parent.parent))

from prpt.core.dotenv import load_dotenv as _load_dotenv_impl


_REPO_ROOT = Path(__file__).parent.parent  # research/ is one level below repo root


def _load_dotenv() -> None:
    """Load .env from repo root. Kept for callers that import this symbol."""
    _load_dotenv_impl(_REPO_ROOT / ".env")


_load_dotenv()


def _normalize_compaction_env() -> None:
    """Pin Claude Code context-management to DEFAULT config for headline runs.

    The Claude desktop app injects ``DISABLE_MICROCOMPACT=1`` into the env of
    every child process it spawns (it is *not* a persistent registry/profile
    var). That turns OFF microcompaction — the incremental trim of old tool
    results — which lets the native ``claude --resume`` arms (BUILTIN/STACKED)
    accumulate more per-turn context before the big auto-compact fires. Measuring
    against that non-default baseline biases the gap in PromptPilot's favour, the
    same class of error as the raised-``model_context_window`` 2.36M artifact.

    Since every downstream subprocess (prpt -> claude/codex) inherits this
    process's ``os.environ``, popping the var here once, before any child spawns,
    propagates the clean default to the whole tree.

    Escape hatch: set ``PROMPTPILOT_KEEP_MICROCOMPACT_OFF=1`` to leave it as-is
    for the Open-item-#6 *contrast* run that documents microcompaction's effect.
    """
    incoming = os.environ.get("DISABLE_MICROCOMPACT")
    if os.environ.get("PROMPTPILOT_KEEP_MICROCOMPACT_OFF") == "1":
        sys.stderr.write(
            "[env] PROMPTPILOT_KEEP_MICROCOMPACT_OFF=1 -> leaving "
            "DISABLE_MICROCOMPACT={0!r} (NON-default contrast run)\n".format(incoming)
        )
        return
    if incoming is not None:
        os.environ.pop("DISABLE_MICROCOMPACT", None)
        sys.stderr.write(
            "[env] removed DISABLE_MICROCOMPACT (was {0!r}) -> microcompaction ON "
            "(default config)\n".format(incoming)
        )
    else:
        sys.stderr.write(
            "[env] DISABLE_MICROCOMPACT not set -> microcompaction ON (default config)\n"
        )


_normalize_compaction_env()

# Force UTF-8 stdout on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from prpt.normalizers.base import build_final_downstream_prompt, build_output_suffix, create_normalizer
from prpt.repo.collector import RepoContextCollector
from prpt.core.types import RepoMetadata
from prpt.session import append_turn, clear_session, load_recent_turns
# Imported by name (not call-site) so the gate loop is unit-testable via monkeypatch.
from prpt.verify import run_verify as _verify_run, build_retry_prompt as _verify_retry_prompt

from agentic_variety_test import (
    _ext, _parse_one, _run_one,
    claude_cost, codex_cost, slm_cost_estimate,
    reap_claude_orphans,
)

HTTPX_DIR = os.environ.get("PROMPTPILOT_TEST_REPO", "C:/projects/httpx")
OUT_DIR = Path(os.environ.get("PROMPTPILOT_OUT_DIR", str(_REPO_ROOT / "research" / "data" / "chain_results_v2")))

# Threshold for "explain" success: at least this many output tokens AND >0 tool_calls
EXPLAIN_MIN_OUTPUT_TOKENS = 300


# ---------------------------------------------------------------------------
# Chains — each turn declares its expected outcome
# ---------------------------------------------------------------------------

CHAINS = [
    {
        "id": "chain1",
        "label": "Bug fix workflow (referential throughout)",
        "description": (
            "Fix a timeout bug, add tests, mirror to async, add async test, "
            "add inline comments. Every turn after T1 is referential."
        ),
        "turns": [
            {
                "raw": "fix the timeout not being passed through to the underlying socket in the sync client",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            {
                "raw": "add a unit test for that fix",
                "expected_files": ["tests/client/test_timeouts.py", "tests/test_timeouts.py", "tests/client/test_client.py"],
                "expected_action": "modify",
            },
            # KNOWN ARTIFACT (FIX_PLAN P1 #4, investigated 2026-04-29):
            # this turn consistently scores 0.00 across all variants because
            # the agent correctly determines no fix is needed -- AsyncClient
            # inherits _set_timeout/build_request from BaseClient, which got
            # the T1 fix. The agent's "no edit needed" is the CORRECT answer;
            # our file-hash scorer records it as 0 because no files were
            # touched. The artifact is uniform across variants so comparative
            # claims are unaffected, but absolute means on chain1 read ~0.10
            # lower than the agent's true success rate would suggest. To fix
            # for real, replace this prompt with one requiring an async-specific
            # change (e.g. an AsyncClient-only method) and re-run all chain1
            # baselines (~$25). Not worth it for current pitch claims.
            {
                "raw": "apply the same fix to the async client",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            {
                "raw": "add a unit test for the async fix as well",
                "expected_files": ["tests/client/test_timeouts.py", "tests/test_timeouts.py", "tests/client/test_async_client.py"],
                "expected_action": "modify",
            },
            {
                "raw": "add a brief inline comment to both fixes explaining why the timeout must be passed explicitly",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
            },
        ],
    },
    {
        "id": "chain2",
        "label": "Feature addition workflow (progressive elaboration)",
        "description": (
            "Add retry-after support, handle date-string variant, cap delay, "
            "write tests, mirror to async."
        ),
        "turns": [
            {
                "raw": "add retry-after header support to the retry logic",
                "expected_files": ["httpx/_transports/default.py", "httpx/_client.py"],
                "expected_action": "modify",
            },
            {
                "raw": "extend that to also handle retry-after as a date string not just seconds",
                "expected_files": ["httpx/_transports/default.py", "httpx/_client.py", "httpx/_utils.py"],
                "expected_action": "modify",
            },
            {
                "raw": "cap the retry delay at 60 seconds to prevent excessive waits",
                "expected_files": ["httpx/_transports/default.py", "httpx/_client.py"],
                "expected_action": "modify",
            },
            {
                "raw": "write tests covering all three retry-after scenarios we just added",
                "expected_files": ["tests/test_retries.py", "tests/client/test_retries.py", "tests/test_transports.py"],
                "expected_action": "modify",
            },
            {
                "raw": "mirror all of those changes to the async client",
                "expected_files": ["httpx/_transports/default.py", "httpx/_client.py"],
                "expected_action": "modify",
            },
        ],
    },
    {
        "id": "chain4",
        "label": "Mixed referential / non-referential (Item #3 gating test)",
        "description": (
            "5-turn chain with mid-chain non-referential prompts (T2, T4) "
            "interleaved with referential modify turns (T3, T5). T1 is also "
            "non-referential. Designed to exercise the referential-classifier "
            "gate in prepare_gated_session(): gate should skip session loading "
            "on T2 and T4, load on T3 and T5. T1 has empty session anyway so "
            "the skip on T1 is a no-op (but still correct classification)."
        ),
        "turns": [
            # T1: fresh bug fix — non-referential. Recent=[] anyway on T1, so
            # the gate's effect is academic, but it should classify correctly.
            {
                "raw": "fix the timeout not being passed through to the underlying socket in the sync client",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            # T2: mid-chain non-referential explain. Gate should SKIP session.
            {
                "raw": "explain how the Client class handles HTTP redirects automatically",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T3: referential modify — refers back to T1's fix. Gate must LOAD session.
            {
                "raw": "add a unit test for that fix",
                "expected_files": ["tests/client/test_timeouts.py", "tests/test_timeouts.py",
                                    "tests/client/test_client.py"],
                "expected_action": "modify",
            },
            # T4: mid-chain non-referential explain. Gate should SKIP session.
            {
                "raw": "explain how httpx serializes JSON request bodies in the Client class",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T5: referential modify — refers to T1 ('the same fix'). Gate must LOAD session.
            {
                "raw": "apply the same fix to the async client",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
            },
        ],
    },
    {
        "id": "smoke",
        "label": "Minimal end-to-end smoke test (2 turns, Haiku-friendly)",
        "description": (
            "Two-turn chain used by tests/test_smoke_chain.py to verify the "
            "pipeline end-to-end: subprocess invocation, JSON parsing, both "
            "scoring branches (explain + modify), promptpilot session load+record. "
            "Designed to run on Haiku for ~$0.10 per pass. Does NOT validate "
            "quality — only that the machinery works."
        ),
        "turns": [
            # T1: explain branch — non-referential, exercises explain scorer
            # (output_tokens >= 300 AND tool_calls > 0).
            {
                "raw": "describe in 2-3 sentences what the Client class in httpx/_client.py is for",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T2: modify branch — referential ("that class"), exercises file-hash
            # scorer + promptpilot session load.
            {
                "raw": "add a one-line docstring to that class summarizing what you just described",
                "expected_files": ["httpx/_client.py"],
                "expected_action": "modify",
            },
        ],
    },
    {
        "id": "chain3",
        "label": "Exploratory then targeted (explain → refactor chain)",
        "description": (
            "Start with an explain prompt, then a series of targeted refactors. "
            "Tests explain→act context handoff."
        ),
        "turns": [
            {
                "raw": "explain how HTTPTransport.handle_request translates between httpx and httpcore request/response types",
                "expected_files": [],
                "expected_action": "explain",
            },
            {
                "raw": "the httpcore.Request construction inside handle_request is duplicated between the sync and async transports, refactor it",
                "expected_files": ["httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            {
                "raw": "rename that helper to _map_httpx_to_httpcore_request and make it a @staticmethod on HTTPTransport",
                "expected_files": ["httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            {
                "raw": "add a Google-style docstring to that method listing every httpx request field it reads and every httpcore.Request field it sets",
                "expected_files": ["httpx/_transports/default.py"],
                "expected_action": "modify",
            },
            {
                "raw": "write a unit test for the extracted method",
                "expected_files": ["tests/test_transports.py", "tests/client/test_client.py"],
                "expected_globs": ["tests/test_transports*.py", "tests/test_transports/**/*.py",
                                    "tests/**/test_transport*.py"],
                "expected_action": "modify",
            },
        ],
    },
    {
        "id": "chain5",
        "label": "Long-task referential decay (15 turns, mixed reference distances)",
        "description": (
            "FIX_PLAN P3 #9 / SLM-harness Step 1 — does quality decay across "
            "long chains, and is the decay correlated with reference distance? "
            "15 turns mix four kinds of work: 4 fresh-topic mods (T1/T2/T5/T9) "
            "at evenly-spread positions for position-decay measurement, 8 "
            "referential mods at distances 1/1/2/5/6/6/11/14 (T3/T6/T7/T10/T11/"
            "T12/T14/T15) for distance-vs-success measurement, and 3 non-ref "
            "explains (T4/T8/T13) as a quality baseline. T15 deliberately uses "
            "a helper-extraction prompt (not 'apply same fix to AsyncClient') "
            "to dodge the chain1 T3 inheritance artifact. Run with_session at "
            "N>=3 first; only add a doubled-window arm if Step 1 shows decay."
        ),
        "turns": [
            # T1 — fresh mod, position 1 (decay anchor)
            {
                "raw": "fix the timeout not being passed through to the underlying socket in the sync client",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
                "referential": False,
            },
            # T2 — fresh mod, unrelated topic, position 2 (decay anchor)
            {
                "raw": "add a default User-Agent header to outgoing requests that includes the httpx version",
                "expected_files": ["httpx/_client.py", "httpx/_models.py", "httpx/__version__.py"],
                "expected_globs": ["httpx/_client.py", "httpx/_models.py", "httpx/__version__.py", "httpx/_config.py"],
                "expected_action": "modify",
                "referential": False,
            },
            # T3 — ref to T1, distance 2
            {
                "raw": "add a unit test for that timeout fix",
                "expected_files": ["tests/test_timeouts.py", "tests/client/test_timeouts.py", "tests/client/test_client.py"],
                "expected_globs": ["tests/**/test_timeout*.py", "tests/**/test_client*.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T4 — explain (non-ref baseline)
            {
                "raw": "explain how the Client class handles HTTP redirects automatically",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T5 — fresh mod, position 5 (decay anchor).
            # Picked deliberately: httpx has NO max-body-size guard at the Client
            # layer (confirmed absent from _client.py/_config.py), so this is a
            # genuine "add a feature" task -- avoids the chain1-T3-style
            # "already implemented" no-op artifact that the original retry-after
            # prompt produced (where T5 hit timeout searching for what to add
            # and T6 declared the work already done).
            {
                "raw": "add a max_request_body_size parameter to the Client constructor that raises an exception if a request body exceeds it",
                "expected_files": ["httpx/_client.py", "httpx/_config.py", "httpx/_exceptions.py"],
                "expected_globs": ["httpx/_client.py", "httpx/_config.py", "httpx/_exceptions.py", "httpx/_models.py"],
                "expected_action": "modify",
                "referential": False,
            },
            # T6 — ref to T5, distance 1
            {
                "raw": "extend that to also enforce a max_response_body_size, raising on responses larger than the limit",
                "expected_files": ["httpx/_client.py", "httpx/_config.py", "httpx/_models.py", "httpx/_exceptions.py"],
                "expected_globs": ["httpx/_client.py", "httpx/_config.py", "httpx/_models.py", "httpx/_exceptions.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T7 — long-distance ref to T1, distance 6
            {
                "raw": "go back to the timeout fix from earlier and add a debug log when the timeout fires",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T8 — explain (non-ref baseline)
            {
                "raw": "explain how httpx serializes JSON request bodies in the Client class",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T9 — fresh mod, position 9 (decay anchor)
            {
                "raw": "add a connect_retries parameter to Client that retries on connection errors",
                "expected_files": ["httpx/_client.py", "httpx/_config.py"],
                "expected_action": "modify",
                "referential": False,
            },
            # T10 — ref to T9, distance 1
            {
                "raw": "add a unit test for connect_retries",
                "expected_files": ["tests/test_retries.py", "tests/client/test_retries.py", "tests/client/test_client.py"],
                "expected_globs": ["tests/**/test_retr*.py", "tests/**/test_client*.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T11 — ref to T5/T6, distance 6
            {
                "raw": "write tests covering the body-size limit scenarios we added earlier",
                "expected_files": ["tests/test_client.py", "tests/client/test_client.py", "tests/test_limits.py"],
                "expected_globs": ["tests/**/test_client*.py", "tests/**/test_limit*.py", "tests/**/test_size*.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T12 — extreme-distance ref to T1, distance 11
            {
                "raw": "the timeout fix from way back at the start of this session — add an inline comment explaining why the explicit passthrough is needed",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T13 — explain (non-ref baseline, position 13)
            {
                "raw": "explain how httpx implements connection pooling",
                "expected_files": [],
                "expected_action": "explain",
            },
            # T14 — ref to T9/T10, distance 5
            {
                "raw": "expand the connect_retries test to also cover an SSL handshake error case",
                "expected_files": ["tests/test_retries.py", "tests/client/test_retries.py"],
                "expected_globs": ["tests/**/test_retr*.py", "tests/**/test_client*.py"],
                "expected_action": "modify",
                "referential": True,
            },
            # T15 — extreme-distance ref to T1, distance 14 (helper extraction, not inheritance trap)
            {
                "raw": "extract the timeout-passthrough logic we added at the start of this session into a small private helper used by both Client and AsyncClient",
                "expected_files": ["httpx/_client.py", "httpx/_transports/default.py"],
                "expected_action": "modify",
                "referential": True,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Repo state helpers
# ---------------------------------------------------------------------------

def reset_repo(cwd: str) -> None:
    """Reset to HEAD and remove untracked files."""
    subprocess.run(["git", "checkout", "--", "."], cwd=cwd, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=cwd, capture_output=True)
    print("  [git] reset to HEAD")


# The `-k` expression for the end-state regression. Default EXCLUDES httpx's
# `test_write_timeout` (asyncio+trio): it asserts a 1e-6s write timeout RAISES, but on a
# fast loopback the write completes first, so it fails non-deterministically REGARDLESS of
# the seeded bug or the fix — leaving it in caps every arm at 0.75. Validated 2026-06-11:
# the remaining timeout tests still catch the seeded bug (buggy tree -> 2 fail; fixed -> 0).
# Override per repo/experiment via PROMPTPILOT_ENDSTATE_PYTEST_K.
_ENDSTATE_PYTEST_K = os.environ.get("PROMPTPILOT_ENDSTATE_PYTEST_K", "timeout and not write_timeout")


def _run_timeout_pytest(cwd: str, timeout_s: int = 180) -> dict:
    """Run the timeout-targeted regression against the LIVE working tree.

    `-k <_ENDSTATE_PYTEST_K>` positively selects the seeded-bug regression while excluding
    the env-flaky write_timeout tests (trust a green run only when it actually exercises a
    timeout test). pytest return codes:
      0 = selected tests passed   1 = failures   5 = no test matched the -k expr
    Bounded by a hard timeout so a wedged/hanging tree (the bug makes timeout tests hang)
    can't stall the harness.
    """
    cmd = [sys.executable, "-m", "pytest", "-k", _ENDSTATE_PYTEST_K, "-q",
           "--no-header", "-p", "no:cacheprovider"]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "PYTEST_TIMEOUT", 124
    except Exception as e:  # pragma: no cover - environment-dependent
        out, rc = "PYTEST_ERROR: {0}".format(e), 125
    return {
        "pytest_cmd": " ".join(cmd),
        "pytest_rc": rc,
        "pytest_passed": rc == 0,          # 0 = a selected timeout test ran green
        "pytest_no_match": rc == 5,        # 5 = no timeout test in the tree
        "pytest_tail": "\n".join(out.splitlines()[-20:]),
    }


def capture_end_state(cwd: str, out_dir: Path, variant: str, run_idx: int,
                      run_pytest: bool = True) -> dict:
    """Capture the repo END STATE after a run, BEFORE it is reset.

    Writes endstate_{variant}_run{run_idx}.json holding the GOLD-STANDARD,
    tool-agnostic signal that transcript-mining only approximates:
      - diff      : `git diff HEAD` — the run's actual edits vs the committed fixture
      - new_files : untracked files the run created (e.g. a new regression test)
      - pytest_*  : a real timeout-targeted pytest run against the live tree
                    (apply-then-verify; the same artifact scores claude and codex
                     identically, so this is the claude-code generalization of the
                     codex-only evidence miner).

    Must be called while the working tree still holds the run's edits — i.e. at the
    end of run_chain_once, before the next run's reset_repo() wipes them.
    """
    es = {"variant": variant, "run": run_idx, "captured": True}
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=cwd, capture_output=True, text=True)
    es["diff"] = diff.stdout or ""
    new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                         cwd=cwd, capture_output=True, text=True)
    es["new_files"] = [f.strip() for f in (new.stdout or "").splitlines() if f.strip()]
    if run_pytest:
        es.update(_run_timeout_pytest(cwd))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "endstate_{0}_run{1}.json".format(variant, run_idx)
    path.write_text(json.dumps(es, indent=2), encoding="utf-8")
    print("  [endstate] captured -> {0} (diff {1}B, pytest rc={2})".format(
        path.name, len(es["diff"]), es.get("pytest_rc", "skip")))
    return es


def hash_file(cwd: str, rel_path: str) -> str | None:
    """SHA256 of a file's contents, or None if it doesn't exist."""
    p = Path(cwd) / rel_path
    if not p.exists():
        return None
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


def _extract_primary_model(out_path: Path) -> str | None:
    """Parse the primary (non-classifier) model claude-code actually used.

    claude-code reports modelUsage as a dict keyed by model name. When promptpilot
    is in the loop, both a Haiku classifier and the main model (Opus/Sonnet)
    appear. We treat the non-haiku entry as the primary; if only haiku is
    present (e.g. smoke test), we return haiku. Returns None if parsing fails.
    """
    try:
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        usage = data.get("modelUsage") or {}
        if not usage:
            return None
        non_haiku = [m for m in usage if "haiku" not in m.lower()]
        if non_haiku:
            return non_haiku[0]
        return list(usage.keys())[0]
    except Exception:
        return None


def snapshot_files(cwd: str, files: list[str]) -> dict[str, str | None]:
    """Snapshot hashes for a list of relative file paths."""
    return {f: hash_file(cwd, f) for f in files}


def snapshot_globs(cwd: str, globs: list[str]) -> set[str]:
    """Return the set of relative paths matching any glob pattern under cwd.

    Used to detect newly-created files after a turn (e.g. a test file the
    agent put in a reasonable-but-unpredicted location).
    """
    import pathlib
    if not globs:
        return set()
    root = pathlib.Path(cwd)
    found: set[str] = set()
    for pattern in globs:
        for p in root.glob(pattern):
            if p.is_file():
                found.add(str(p.relative_to(root)).replace("\\", "/"))
    return found


def files_changed(cwd: str, before: dict[str, str | None]) -> list[str]:
    """Return the subset of `before` whose hash differs after."""
    changed = []
    for f, h_before in before.items():
        h_after = hash_file(cwd, f)
        if h_before != h_after:
            changed.append(f)
    return changed


# ---------------------------------------------------------------------------
# Task success scoring
# ---------------------------------------------------------------------------

def score_turn(turn_def: dict, before_hashes: dict[str, str | None],
               before_globs: set[str], cwd: str, usage: dict) -> dict:
    """
    Returns:
      success     — 1 (full), 0.5 (partial), 0 (none)
      bailed      — True if tool_calls == 0
      changed     — list of expected files that were modified
    """
    bailed = usage.get("tool_calls", 0) == 0

    if turn_def["expected_action"] == "explain":
        # Explain success: produced enough output AND made at least one tool call
        out = usage.get("output_tokens", 0)
        ok = (out >= EXPLAIN_MIN_OUTPUT_TOKENS) and (not bailed)
        return {
            "success": 1.0 if ok else 0.0,
            "bailed": bailed,
            "changed": [],
        }

    # Modify success: at least one expected file was changed
    changed = files_changed(cwd, before_hashes)
    if changed:
        if len(changed) == len(turn_def["expected_files"]):
            score = 1.0
        else:
            score = 0.5
        return {"success": score, "bailed": bailed, "changed": changed}

    # Fallback: did the agent create a reasonable new file matching expected_globs?
    # (e.g. a test file in a sensible subdir the scorer didn't explicitly list)
    globs = turn_def.get("expected_globs", [])
    if globs:
        after_globs = snapshot_globs(cwd, globs)
        new_files = sorted(after_globs - before_globs)
        if new_files:
            return {"success": 1.0, "bailed": bailed, "changed": new_files}

    return {"success": 0.0, "bailed": bailed, "changed": []}


# ---------------------------------------------------------------------------
# Prompt preparation (same as v1)
# ---------------------------------------------------------------------------

# Module-level normalizer name, set by main() from --normalizer. Default "slm"
# preserves prior behavior (auto-detect prefers Anthropic SDK with API key).
_NORMALIZER_NAME = "slm"


def _make_normalizer():
    return create_normalizer(_NORMALIZER_NAME, load_repo_content=True)


def _optimize(raw: str, cwd: str, tool: str) -> dict:
    repo = RepoContextCollector().collect(cwd)
    norm = _make_normalizer()
    normalized = norm.normalize(raw, repo)
    # v2 #5 benchmark hook: when the normalizer is v2 (carries _last_spec),
    # pass spec.target_files through so build_final_downstream_prompt can
    # append the "[likely files: ...]" hint. Hint emission is itself gated
    # by PROMPTPILOT_USE_TARGET_HINT=1 so arm A vs arm B is a clean env-var
    # flip, not a code change.
    spec = getattr(norm, "_last_spec", None)
    target_files = getattr(spec, "target_files", None) if spec is not None else None
    grounded = build_final_downstream_prompt(normalized, repo, target_files=target_files)
    intent = getattr(norm, "_last_intent", "act")
    scope = getattr(norm, "_last_scope", "localized")
    suffix_tool = "anthropic" if tool == "claude-code" else tool
    suffix = build_output_suffix(scope, suffix_tool) if intent == "act" else ""
    optimized = grounded + "\n\n" + suffix if suffix else grounded
    return {
        "raw": raw,
        "rewrite": normalized.normalized_prompt,
        "grounded": grounded,
        "optimized": optimized,
        "intent": intent,
        "scope": scope,
        # Stash the normalizer + normalized so record_to_session() can route
        # through cli._build_assistant_record (which reads spec.memory_record
        # on v2 normalizers). Without this the harness would still build the
        # legacy "Modified: {files}\n{rewrite[:400]}" record and silently
        # diverge from production cli.py behavior.
        "_normalizer": norm,
        "_normalized": normalized,
    }


def prepare_no_session(raw: str, cwd: str, tool: str) -> dict:
    return {**_optimize(raw, cwd, tool), "had_history": False}


def prepare_raw(raw: str, cwd: str, tool: str) -> dict:
    """Ground-zero baseline: no SLM rewrite, no repo context, no session, no output suffix.
    What the user sees if they type the prompt straight into claude-code."""
    return {
        "raw": raw,
        "rewrite": raw,
        "grounded": raw,
        "optimized": raw,
        "intent": "act",
        "scope": "localized",
        "had_history": False,
    }


def prepare_with_session(raw: str, cwd: str, tool: str) -> dict:
    recent = load_recent_turns(cwd)
    if recent:
        history = "\n".join(recent)
        prompt_for_slm = (
            "[Recent conversation]\n{history}\n\n[Current request]\n{prompt}"
        ).format(history=history, prompt=raw)
    else:
        prompt_for_slm = raw
    result = _optimize(prompt_for_slm, cwd, tool)
    result["raw"] = raw
    result["had_history"] = bool(recent)
    result["referential"] = None      # not classified for this variant
    result["gate_skipped"] = False
    return result


# Lazy singleton: avoids spinning up an Anthropic client at import time
# (matters when ANTHROPIC_API_KEY isn't set, e.g. for --reprint runs).
_referential_classifier = None


def _is_referential(prompt: str) -> bool:
    """Haiku-backed gate: True iff prompt back-references prior turns.

    Routes through the same normalizer factory as _make_normalizer() so the
    referential classifier honors --normalizer (slm/slm-anthropic/slm-openai/
    slm-subscription). Fail-safe: returns True on any error so we never silently
    drop session memory on a turn that needed it.
    """
    global _referential_classifier
    if _referential_classifier is None:
        _referential_classifier = create_normalizer(_NORMALIZER_NAME, load_repo_content=False)
    return _referential_classifier.is_referential(prompt)


def prepare_gated_session(raw: str, cwd: str, tool: str) -> dict:
    """Like prepare_with_session, but gates load_recent_turns() on a Haiku
    referential classifier — skips loading session entirely for self-contained
    prompts (T1, fresh questions). Historical chain4 N=10 saving was ~26%
    input tokens; chain4 N=10 retest 2026-05-17 with v2 clean memory_record
    measured the saving at ~5% (session is now short, so the gate has less
    to skip). See `research/data/_gate_chain4_n10/COMPARISON.md`.
    """
    referential = _is_referential(raw)
    if referential:
        recent = load_recent_turns(cwd)
    else:
        recent = []     # gate skipped session load
    if recent:
        history = "\n".join(recent)
        prompt_for_slm = (
            "[Recent conversation]\n{history}\n\n[Current request]\n{prompt}"
        ).format(history=history, prompt=raw)
    else:
        prompt_for_slm = raw
    result = _optimize(prompt_for_slm, cwd, tool)
    result["raw"] = raw
    result["had_history"] = bool(recent)
    result["referential"] = referential
    result["gate_skipped"] = (not referential)
    return result


def record_to_session(cwd: str, raw: str, prepared: dict) -> None:
    """Record (user, assistant) turn pair into the promptpilot session.

    Builds the assistant record by routing through cli._build_assistant_record,
    which prefers v2 spec.memory_record (a clean one-sentence summary from
    the SLM) over the verbose rewrite, then prefixes the adapter's modified-
    files list. Falls back to the rewrite for v1 normalizers. Identical to
    the production cli.py path — keeps harness measurements faithful to
    real-world session behavior.

    `prepared` must carry `_normalizer` + `_normalized` (added by _optimize);
    when `prepare_raw` is used, the assistant turn is not recorded anyway.
    """
    from prpt.adapters.shell import _git_modified_files
    from prpt.cli import _build_assistant_record
    append_turn(cwd, "user", raw)
    modified = _git_modified_files(cwd)
    record = _build_assistant_record(
        prepared["_normalizer"], prepared["_normalized"], modified)
    append_turn(cwd, "assistant", record)


# ---------------------------------------------------------------------------
# Quota-exhaustion guard
# ---------------------------------------------------------------------------

class QuotaExhausted(Exception):
    """Raised when a downstream tool call is rejected for hitting a usage/quota
    limit. Lets run_chain_full abort cleanly instead of recording the rejected
    turn (and every subsequent one) as a phantom 0-success data point.

    Real incident 2026-05-21: a codex 3-way run hit the ChatGPT usage limit
    partway through the BUILTIN arm; 20 quota-rejected turns were logged as
    0-success and polluted the aggregate. See
    research/data/_codex_session_3way/COMPARISON.md.
    """


def _quota_exhausted(out_path: Path, tool: str) -> bool:
    """Detect a usage/quota-limit rejection in a tool's output.

    codex emits JSONL with `{"type":"turn.failed","error":{"message":"...usage
    limit..."}}` (and a matching `{"type":"error",...}`). claude-code surfaces
    rate/usage limits in its JSON `result`/`error` text.

    IMPORTANT (2026-06-09 false-positive fix): for codex we now scope the match to
    an actual `turn.failed`/`error` EVENT's own message — NOT a substring anywhere
    in the file. On timeout/retry-themed chains the AGENT's own output legitimately
    contains "try again", "rate limit", "retry-after", etc.; the old whole-file
    `"turn.failed" in low and any(sig in low)` check fired on that prose and aborted
    healthy runs (observed throughout the 2026-06 seeded-bug runs). We also restrict
    to usage-limit-specific phrasing and drop the over-broad bare "rate limit"/"quota"
    substrings. Fail-open: an unrecognised cap phrasing yields a recorded 0-turn
    rather than a false abort — the safer failure mode.
    """
    QUOTA_PHRASES = ("usage limit", "quota exceeded", "exceeded your",
                     "purchase more credits", "try again at", "upgrade to pro")
    try:
        text = Path(out_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False

    if tool == "codex":
        # Only a turn.failed / error EVENT's message counts — never the agent's
        # item.completed / agent_message prose.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") not in ("turn.failed", "error"):
                continue
            err = ev.get("error")
            msg = (err.get("message") if isinstance(err, dict) else None) or ev.get("message") or ""
            if any(p in str(msg).lower() for p in QUOTA_PHRASES):
                return True
        return False

    # claude-code / generic: single JSON object; check its result/error text.
    low = text.lower()
    return any(p in low for p in ("usage limit", "rate_limit_error",
                                  "exceeded your", "quota exceeded"))


# ---------------------------------------------------------------------------
# Single-chain runners (one full pass through all turns)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Verify-gate measurement arm (OPTIMIZATION_LEVERS Lever #1)
# ---------------------------------------------------------------------------

def _git_changed_for_gate(cwd: str) -> list[str]:
    """Tracked files changed vs HEAD (run_verify augments these with untracked new files)."""
    try:
        p = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=cwd,
                           capture_output=True, text=True, timeout=20)
        return [f.strip() for f in (p.stdout or "").splitlines() if f.strip()]
    except Exception:
        return []


def _gate_merge_usage(usage: dict, retry_usage: dict, tool: str) -> None:
    """Charge a retry's tokens to the turn — the honest cost of the gate (incl. uncached)."""
    for k in ("input_tokens", "output_tokens", "cached_tokens", "uncached_tokens", "tool_calls"):
        usage[k] = usage.get(k, 0) + retry_usage.get(k, 0)
    if tool == "claude-code" and retry_usage.get("total_cost_usd"):
        usage["total_cost_usd"] = usage.get("total_cost_usd", 0.0) + retry_usage["total_cost_usd"]


def run_verify_gate_turn(tool: str, out_dir: Path, run_idx: int, turn: int, ext: str,
                         usage: dict, max_retries: int = 1) -> dict:
    """After an agent turn, run the verify-gate: test the changed files; on a real failure
    do up to `max_retries` SLM-bypassed retries (re-invoke the agent with the failure) and
    MERGE the retry tokens into `usage`. Returns gate metadata for the run record.

    Uses the SHIPPED prpt.verify code (run_verify / build_retry_prompt) so the measurement
    reflects the real gate, and charges retry tokens to the arm so cost is counted honestly.
    """
    repo = RepoMetadata(cwd=HTTPX_DIR, test_framework="pytest")
    changed = _git_changed_for_gate(HTTPX_DIR)
    result = _verify_run(repo, changed)
    retries = 0
    retry_tokens = {"input_tokens": 0, "output_tokens": 0, "uncached_tokens": 0, "tool_calls": 0}
    while result.ran and not result.passed and retries < max_retries:
        retries += 1
        print("    [gate] verify FAILED (rc={0}); retry {1}/{2}...".format(
            result.returncode, retries, max_retries))
        rpath = out_dir / "run{0}_with_gate_t{1}_retry{2}{3}".format(run_idx, turn, retries, ext)
        _run_one(_verify_retry_prompt(result, changed), rpath, HTTPX_DIR, tool, session_id=None)
        if _quota_exhausted(rpath, tool):
            raise QuotaExhausted("quota hit during gate retry at run{0}/T{1}".format(run_idx, turn))
        ru = _parse_one(rpath, tool)
        for k in retry_tokens:
            retry_tokens[k] += ru.get(k, 0)
        _gate_merge_usage(usage, ru, tool)
        changed = _git_changed_for_gate(HTTPX_DIR)
        result = _verify_run(repo, changed)
    status = ("pass" if result.passed else "fail") if result.ran else "skip"
    print("    [gate] {0} (retries={1}, retry_uncached={2:,})".format(
        status, retries, retry_tokens["uncached_tokens"]))
    return {
        "verify_ran": result.ran, "verify_passed": result.passed,
        "verify_rc": result.returncode, "verify_skipped": result.skipped_reason,
        "retries": retries, "retry_tokens": retry_tokens,
    }


def run_chain_once(chain: dict, tool: str, variant: str, run_idx: int,
                   out_dir: Path) -> list[dict]:
    """
    Run all turns of `chain` once. variant ∈ {"no_session", "with_session", "with_gate", ...}.
    Returns per-turn result dicts.
    """
    assert variant in ("no_session", "with_session", "with_gate", "raw", "builtin", "stacked", "slm_native", "gated_session")
    # Print the resolved model up front so wrong-model invocations are visible
    # on the first turn rather than after the rolled-up analysis. (FIX_PLAN P1 #3)
    if tool == "claude-code":
        print("  [model] {0} (CLAUDE_MODEL env -> claude --model)".format(
            os.environ.get("CLAUDE_MODEL", "opus")))
    # Defensive: kill any orphan claude.exe zombies from a prior interrupted
    # run before we start. The per-timeout reaper inside run_claude_code is
    # the primary mechanism; this is belt-and-suspenders for fresh-start safety.
    try:
        n = reap_claude_orphans()
        if n:
            print("  [reap] killed {0} orphan claude.exe processes".format(n))
    except Exception:
        pass
    clear_session(HTTPX_DIR)
    reset_repo(HTTPX_DIR)

    # Track claude-code built-in session ID across turns for variants that use it
    uses_builtin = variant in ("builtin", "stacked", "slm_native")
    builtin_session_id: str | None = None

    results = []
    ext = _ext(tool)
    for i, turn_def in enumerate(chain["turns"], 1):
        raw = turn_def["raw"]
        print("  [{v}/run{r}] T{i}: {p}...".format(
            v=variant, r=run_idx, i=i, p=raw[:55]))

        # Snapshot expected files BEFORE this turn so we can detect this turn's changes
        before = snapshot_files(HTTPX_DIR, turn_def["expected_files"])
        before_globs = snapshot_globs(HTTPX_DIR, turn_def.get("expected_globs", []))

        # Prepare prompt
        if variant == "no_session":
            prepared = prepare_no_session(raw, HTTPX_DIR, tool)
        elif variant == "raw":
            prepared = prepare_raw(raw, HTTPX_DIR, tool)
        elif variant == "builtin":
            # Arm B: built-in session only, raw prompts (no promptpilot optimizer).
            prepared = prepare_raw(raw, HTTPX_DIR, tool)
        elif variant == "stacked":
            # Arm C: promptpilot session + built-in session (stacked memory).
            prepared = prepare_with_session(raw, HTTPX_DIR, tool)
        elif variant == "slm_native":
            # Isolation arm ("STACKED" in the handoff/memory vocabulary): SLM
            # rewrite WITHOUT a PromptPilot bounded session + the tool's NATIVE
            # resume for continuity. WITH_SESSION vs slm_native = pure
            # bounded-vs-native session-mechanism effect, holding the SLM rewrite
            # constant. Distinct from `stacked`, which layers BOTH mechanisms.
            # No record_to_session() below (line gating list) — continuity comes
            # from native resume, not an in-prompt bounded summary.
            prepared = prepare_no_session(raw, HTTPX_DIR, tool)
        elif variant == "gated_session":
            # Item #3: promptpilot session, but skip load_recent_turns() on
            # non-referential prompts (Haiku classifier).
            prepared = prepare_gated_session(raw, HTTPX_DIR, tool)
        elif variant == "with_gate":
            # Lever #1: WITH_SESSION + the verify-gate (an add-on, not a different prep).
            # Isolates the gate's effect vs with_session.
            prepared = prepare_with_session(raw, HTTPX_DIR, tool)
        else:
            prepared = prepare_with_session(raw, HTTPX_DIR, tool)

        # Run downstream tool
        out_path = out_dir / "run{0}_{1}_t{2}{3}".format(run_idx, variant, i, ext)
        wall_t, _ = _run_one(prepared["optimized"], out_path, HTTPX_DIR, tool,
                             session_id=builtin_session_id if uses_builtin else None)

        # Quota guard: if the tool rejected this turn for a usage/quota limit,
        # abort the whole run NOW rather than recording this turn (and every
        # subsequent one) as a phantom 0-success. Every following call would
        # fail identically until the quota window resets. (2026-05-21 incident.)
        if _quota_exhausted(out_path, tool):
            raise QuotaExhausted(
                "{tool} hit a usage/quota limit at {v}/run{r}/T{i} — "
                "aborting. Re-run after the quota window resets.".format(
                    tool=tool, v=variant, r=run_idx, i=i))

        usage = _parse_one(out_path, tool)

        # For variants using built-in session, capture session_id for next turn.
        # claude-code emits a single JSON object with a "session_id" field;
        # codex emits JSONL where the first event is
        # {"type":"thread.started","thread_id":"<uuid>"}. Only capture the
        # session id from turn 1 (the session-creating turn) — resuming turns
        # reuse the same id, so don't overwrite it with a later turn's id.
        if uses_builtin and builtin_session_id is None:
            try:
                if tool == "claude-code":
                    with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                        sid = json.load(f).get("session_id")
                else:  # codex JSONL
                    sid = None
                    with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            if ev.get("type") == "thread.started" and ev.get("thread_id"):
                                sid = ev["thread_id"]
                                break
                if sid:
                    builtin_session_id = sid
            except Exception:
                pass

        # Verify-gate arm: test the changed files; on a real failure do a capped,
        # SLM-bypassed retry and charge its tokens into `usage` (so the uncached/cost
        # computed below include the gate's cost). End-state success is scored separately
        # via capture_end_state — the per-turn score below is non-verdict.
        gate_meta = None
        if variant == "with_gate":
            gate_meta = run_verify_gate_turn(tool, out_dir, run_idx, i, ext, usage)

        # Score this turn (per-turn diff; non-verdict — the verdict is end-state)
        score = score_turn(turn_def, before, before_globs, HTTPX_DIR, usage)

        # Record to promptpilot session for variants that use it
        if variant in ("with_session", "with_gate", "stacked", "gated_session"):
            record_to_session(HTTPX_DIR, raw, prepared)

        slm_cost = 0.0 if variant in ("raw", "builtin") else slm_cost_estimate(raw, prepared["grounded"])
        # gated_session pays for one extra Haiku classifier call per turn (~$0.00017).
        if variant == "gated_session":
            slm_cost += 0.00017

        # Cost + uncached instrumentation (2026-06-07 session re-test). The gross
        # `input_tokens` is ~95% cached re-reads of the transcript, so it overstates
        # the real per-turn context cost. Record UNCACHED input (the real
        # incremental context the model paid full price for) and a cache-aware
        # per-turn tool cost. claude_cost() uses claude-code's self-reported
        # total_cost_usd (real, model-accurate even on Max OAuth); codex_cost() is a
        # NOTIONAL o4-mini proxy — gpt-5.5 rates are not available locally, so do not
        # treat codex $ as authoritative (report codex in uncached TOKENS instead).
        # Canonical uncached now comes from the parser (parse_usage_*); fall back to the
        # gross-minus-cached formula for older records that predate the uncached_tokens field.
        uncached_input = usage.get("uncached_tokens",
                                   usage["input_tokens"] - usage.get("cached_tokens", 0))
        downstream_cost = claude_cost(usage) if tool == "claude-code" else codex_cost(usage)

        # Capture which model was requested vs which model claude-code actually
        # used. Default changed sonnet -> opus 2026-05-16; this catches the
        # "I forgot to set CLAUDE_MODEL=sonnet and accidentally burned opus
        # quota" failure mode (inverse of the original wrong-model failure)
        # immediately on turn 1, not after the rolled-up analysis.
        # See FIX_PLAN.md P1 #3.
        model_resolved = os.environ.get("CLAUDE_MODEL", "opus")
        model_used = _extract_primary_model(out_path) if tool == "claude-code" else None

        results.append({
            "turn": i,
            "raw": raw,
            "expected_action": turn_def["expected_action"],
            "expected_files": turn_def["expected_files"],
            "intent": prepared["intent"],
            "scope": prepared["scope"],
            "had_history": prepared["had_history"],
            "referential": prepared.get("referential"),
            "gate_skipped": prepared.get("gate_skipped", False),
            "prompt_chars": len(prepared["optimized"]),
            "wall_t": wall_t,
            "usage": usage,
            "uncached_input": uncached_input,
            "slm_cost": slm_cost,
            "downstream_cost": downstream_cost,
            "total_cost": downstream_cost + slm_cost,
            "score": score,
            "gate": gate_meta,
            "model_resolved": model_resolved,
            "model_used": model_used,
        })
        print("    success={s:.1f}  tool_calls={tc}  in={i:,}  out={o:,}  "
              "bailed={b}  changed={c}".format(
                  s=score["success"], tc=usage["tool_calls"],
                  i=usage["input_tokens"], o=usage["output_tokens"],
                  b="Y" if score["bailed"] else "N",
                  c=len(score["changed"])))

    # Capture the gold-standard end-state (diff + live timeout-pytest) while the
    # working tree still holds this run's edits — the next run's reset_repo() wipes
    # them. Best-effort: a capture failure must never lose the run's scored results.
    # Opt out with CAPTURE_END_STATE=0 (e.g. when the verify pytest is too slow).
    if os.environ.get("CAPTURE_END_STATE", "1") != "0":
        try:
            run_pytest = os.environ.get("CAPTURE_END_STATE_PYTEST", "1") != "0"
            capture_end_state(HTTPX_DIR, out_dir, variant, run_idx, run_pytest=run_pytest)
        except Exception as e:  # pragma: no cover - defensive
            print("  [endstate] capture failed (non-fatal): {0}".format(e))

    clear_session(HTTPX_DIR)
    return results


# ---------------------------------------------------------------------------
# Aggregation across N runs
# ---------------------------------------------------------------------------

def aggregate_runs(runs: list[list[dict]]) -> list[dict]:
    """
    Average per-turn metrics across N runs of the same chain+variant.
    Each `runs[k]` is a list of per-turn dicts.
    """
    if not runs:
        return []
    n_turns = len(runs[0])
    aggregated = []
    for i in range(n_turns):
        per_run = [r[i] for r in runs]
        success_vals = [pr["score"]["success"] for pr in per_run]
        bailed_vals = [1 if pr["score"]["bailed"] else 0 for pr in per_run]
        tool_calls = [pr["usage"]["tool_calls"] for pr in per_run]
        in_toks = [pr["usage"]["input_tokens"] for pr in per_run]
        out_toks = [pr["usage"]["output_tokens"] for pr in per_run]
        wall = [pr["wall_t"] for pr in per_run]
        # Uncached input + cost (2026-06-07 re-test). Back-compat: older run files
        # predate these per-turn keys, so fall back to deriving them from `usage`.
        uncached = [pr.get("uncached_input",
                           pr["usage"]["input_tokens"] - pr["usage"].get("cached_tokens", 0))
                    for pr in per_run]
        cached = [pr["usage"].get("cached_tokens", 0) for pr in per_run]
        total_cost = [pr.get("total_cost", pr.get("downstream_cost", 0.0) + pr.get("slm_cost", 0.0))
                      for pr in per_run]
        aggregated.append({
            "turn": per_run[0]["turn"],
            "raw": per_run[0]["raw"],
            "expected_action": per_run[0]["expected_action"],
            "had_history": per_run[0]["had_history"],
            "n_runs": len(per_run),
            "success_mean": statistics.mean(success_vals),
            "bailed_rate": statistics.mean(bailed_vals),
            "tool_calls_mean": statistics.mean(tool_calls),
            "tool_calls_stdev": statistics.stdev(tool_calls) if len(tool_calls) > 1 else 0.0,
            "input_tokens_mean": statistics.mean(in_toks),        # GROSS (≈95% cached) — contrast only
            "uncached_input_mean": statistics.mean(uncached),     # real incremental context (primary)
            "cached_input_mean": statistics.mean(cached),
            "output_tokens_mean": statistics.mean(out_toks),
            "output_tokens_stdev": statistics.stdev(out_toks) if len(out_toks) > 1 else 0.0,
            "total_cost_mean": statistics.mean(total_cost),       # cache-aware $/turn (claude real; codex notional)
            "wall_t_mean": statistics.mean(wall),
        })
    return aggregated


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _pct_delta(no_val: float, with_val: float) -> float:
    """Percent change from no_val to with_val. Positive = WITH_SESSION higher."""
    if no_val == 0:
        return 0.0
    return (with_val - no_val) / no_val * 100


def print_chain_summary(chain: dict, no_agg: list[dict], with_agg: list[dict],
                        tool: str, n_runs: int,
                        gated_agg: list[dict] | None = None) -> None:
    print()
    print("=" * 96)
    print("  Chain {id}: {label}".format(**chain))
    print("  {0}".format(chain["description"]))
    print("  tool={0}  runs={1}".format(tool, n_runs))
    print("=" * 96)
    print()

    # Per-turn table — primary metrics
    hdr = "  {:<4} {:<38} {:>8} {:>8} {:>9} {:>9} {:>9} {:>9}".format(
        "Turn", "Raw prompt (truncated)",
        "succ N", "succ W",
        "calls N", "calls W",
        "in N", "in W")
    print(hdr)
    print("  " + "-" * 92)

    sums = {"calls_n": 0, "calls_w": 0, "in_n": 0, "in_w": 0,
            "out_n": 0, "out_w": 0, "succ_n": 0.0, "succ_w": 0.0,
            "bail_n": 0.0, "bail_w": 0.0}

    for no, ws in zip(no_agg, with_agg):
        label = no["raw"][:36] + (".." if len(no["raw"]) > 36 else "")
        marker = "  " if ws["had_history"] else "* "  # * = baseline (no history)
        print("  {:<4} {}{:<36} {:>8.2f} {:>8.2f} {:>9.1f} {:>9.1f} {:>9,} {:>9,}".format(
            no["turn"], marker, label,
            no["success_mean"], ws["success_mean"],
            no["tool_calls_mean"], ws["tool_calls_mean"],
            int(no["input_tokens_mean"]), int(ws["input_tokens_mean"]),
        ))
        sums["calls_n"] += no["tool_calls_mean"]
        sums["calls_w"] += ws["tool_calls_mean"]
        sums["in_n"] += no["input_tokens_mean"]
        sums["in_w"] += ws["input_tokens_mean"]
        sums["out_n"] += no["output_tokens_mean"]
        sums["out_w"] += ws["output_tokens_mean"]
        sums["succ_n"] += no["success_mean"]
        sums["succ_w"] += ws["success_mean"]
        sums["bail_n"] += no["bailed_rate"]
        sums["bail_w"] += ws["bailed_rate"]

    print("  " + "-" * 92)
    n_turns = len(no_agg)
    print("  {:<4} {:<38} {:>8.2f} {:>8.2f} {:>9.1f} {:>9.1f} {:>9,} {:>9,}".format(
        "TOT", "(sum / mean for success)",
        sums["succ_n"] / n_turns, sums["succ_w"] / n_turns,
        sums["calls_n"], sums["calls_w"],
        int(sums["in_n"]), int(sums["in_w"]),
    ))
    print()

    # Secondary metrics + verdict
    print("  Secondary: output tokens (informational only)")
    print("    NO_SESSION   total out: {0:>9,}".format(int(sums["out_n"])))
    print("    WITH_SESSION total out: {0:>9,}".format(int(sums["out_w"])))
    print()

    # Verdict — primary metrics
    succ_delta = sums["succ_w"] - sums["succ_n"]
    calls_delta_pct = _pct_delta(sums["calls_n"], sums["calls_w"])
    in_delta_pct = _pct_delta(sums["in_n"], sums["in_w"])
    bail_n_pct = sums["bail_n"] / n_turns * 100
    bail_w_pct = sums["bail_w"] / n_turns * 100

    print("  PRIMARY VERDICT")
    print("    Mean success      NO={0:.2f}  WITH={1:.2f}  Δ={2:+.2f}".format(
        sums["succ_n"] / n_turns, sums["succ_w"] / n_turns, succ_delta / n_turns))
    print("    Total tool_calls  NO={0:.0f}  WITH={1:.0f}  Δ={2:+.1f}%".format(
        sums["calls_n"], sums["calls_w"], calls_delta_pct))
    print("    Total input_toks  NO={0:,.0f}  WITH={1:,.0f}  Δ={2:+.1f}%".format(
        sums["in_n"], sums["in_w"], in_delta_pct))
    print("    Bail rate         NO={0:.0f}%  WITH={1:.0f}%".format(bail_n_pct, bail_w_pct))
    print()

    # Headline judgment
    if succ_delta / n_turns >= 0.15:
        print("  >> WITH_SESSION significantly improved task success "
              "(+{0:.0f}%)".format(succ_delta / n_turns * 100))
    elif succ_delta / n_turns <= -0.15:
        print("  >> WITH_SESSION HURT task success "
              "({0:.0f}%)".format(succ_delta / n_turns * 100))
    elif calls_delta_pct <= -15 and abs(succ_delta) <= 0.10:
        print("  >> WITH_SESSION reduced exploration cost "
              "({0:.0f}% fewer tool calls) at equal success".format(-calls_delta_pct))
    elif calls_delta_pct >= 15 and abs(succ_delta) <= 0.10:
        print("  >> WITH_SESSION increased exploration cost "
              "(+{0:.0f}% tool calls) at equal success".format(calls_delta_pct))
    else:
        print("  >> No clear winner on primary metrics.")
    print()

    # Optional third arm — GATED_SESSION
    if gated_agg is not None:
        g_succ = sum(g["success_mean"] for g in gated_agg)
        g_calls = sum(g["tool_calls_mean"] for g in gated_agg)
        g_in = sum(g["input_tokens_mean"] for g in gated_agg)
        g_out = sum(g["output_tokens_mean"] for g in gated_agg)
        g_bail = sum(g["bailed_rate"] for g in gated_agg) / n_turns * 100
        print("  GATED_SESSION arm (referential classifier gate)")
        print("    Mean success      G={0:.2f}".format(g_succ / n_turns))
        print("    Total tool_calls  G={0:.0f}".format(g_calls))
        print("    Total input_toks  G={0:,.0f}".format(g_in))
        print("    Total output_toks G={0:,.0f}".format(g_out))
        print("    Bail rate         G={0:.0f}%".format(g_bail))
        print()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_run(out_dir: Path, variant: str, run_idx: int, results: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "{0}_run{1}.json".format(variant, run_idx)
    path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


def load_run(out_dir: Path, variant: str, run_idx: int) -> list[dict] | None:
    """Load a previously-saved per-run result, or None if absent.

    Enables cross-quota-window resume on codex: a fully-completed run is loaded
    from disk instead of re-executed, so a window that only permits ~3-4 runs
    still makes forward progress instead of redoing run 1 every time. The data
    is identical to a single-sitting run (same arms / N / fixture / scoring).
    """
    path = out_dir / "{0}_run{1}.json".format(variant, run_idx)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_summary(out_dir: Path, no_agg: list[dict] | None, with_agg: list[dict],
                 chain_id: str, tool: str, n_runs: int,
                 gated_agg: list[dict] | None = None,
                 builtin_agg: list[dict] | None = None,
                 slm_native_agg: list[dict] | None = None,
                 stacked_agg: list[dict] | None = None) -> None:
    summary = {
        "chain": chain_id,
        "tool": tool,
        "n_runs": n_runs,
        "no_session_agg": no_agg,
        "with_session_agg": with_agg,
    }
    if gated_agg is not None:
        summary["gated_session_agg"] = gated_agg
    if builtin_agg is not None:
        summary["builtin_session_agg"] = builtin_agg
    if slm_native_agg is not None:
        summary["slm_native_agg"] = slm_native_agg
    if stacked_agg is not None:
        summary["stacked_agg"] = stacked_agg
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")


def load_summary(out_dir: Path) -> dict | None:
    p = out_dir / "summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Dry-run: show prepared prompts and expected files only
# ---------------------------------------------------------------------------

def dry_run_chain(chain: dict, tool: str) -> None:
    print()
    print("=" * 96)
    print("  Chain {id}: {label}".format(**chain))
    print("=" * 96)
    clear_session(HTTPX_DIR)
    reset_repo(HTTPX_DIR)

    for i, turn_def in enumerate(chain["turns"], 1):
        raw = turn_def["raw"]
        print("\n  Turn {0}: {1}".format(i, raw))
        print("    expected_action: {0}".format(turn_def["expected_action"]))
        print("    expected_files:  {0}".format(turn_def["expected_files"] or "(none)"))

        no_prep = prepare_no_session(raw, HTTPX_DIR, tool)
        with_prep = prepare_with_session(raw, HTTPX_DIR, tool)
        record_to_session(HTTPX_DIR, raw, with_prep)

        print("    NO   intent={0} scope={1}  {2} chars".format(
            no_prep["intent"], no_prep["scope"], len(no_prep["optimized"])))
        print("    WITH intent={0} scope={1}  {2} chars  history={3}".format(
            with_prep["intent"], with_prep["scope"],
            len(with_prep["optimized"]), with_prep["had_history"]))

    clear_session(HTTPX_DIR)
    reset_repo(HTTPX_DIR)


# ---------------------------------------------------------------------------
# Main runner: per chain x tool, runs N passes per variant, aggregates
# ---------------------------------------------------------------------------

def run_chain_full(chain: dict, tool: str, n_runs: int,
                   include_gated: bool = False,
                   skip_no_session: bool = False,
                   include_builtin: bool = False,
                   skip_with_session: bool = False,
                   include_slm_native: bool = False,
                   include_stacked: bool = False,
                   ) -> tuple[list[dict] | None, list[dict] | None, list[dict] | None]:
    """Run N passes of NO_SESSION (unless `skip_no_session`) + N of WITH_SESSION
    (+ optionally GATED_SESSION when `include_gated=True`, + optionally the
    native BUILTIN session arm when `include_builtin=True`). Returns aggregated
    per-turn results for (no, with, gated); `builtin` is persisted to per-run
    files + summary.json but not in the return tuple (kept 3-wide for
    backward-compat). `no_agg` is None when skipped, `gated_agg` None when not run.
    """
    out_dir = OUT_DIR / tool / chain["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    no_runs: list | None = None
    with_runs: list = []
    gated_runs: list = []
    builtin_runs: list = []
    slm_native_runs: list = []
    stacked_runs: list = []
    quota_hit = False

    def _agg_complete(runs):
        """Aggregate an arm only if it ran the full n_runs — a partially-failed
        arm (quota abort mid-arm) is discarded rather than reported."""
        return aggregate_runs(runs) if runs and len(runs) == n_runs else None

    try:
        if skip_no_session:
            print("\n--- NO_SESSION arm skipped (--skip-no-session) ---")
        else:
            print("\n--- NO_SESSION ({0} runs) ---".format(n_runs))
            no_runs = []
            for r in range(1, n_runs + 1):
                results = run_chain_once(chain, tool, "no_session", r, out_dir)
                save_run(out_dir, "no_session", r, results)
                no_runs.append(results)

        if skip_with_session:
            print("\n--- WITH_SESSION arm skipped (--skip-with-session) ---")
        else:
            print("\n--- WITH_SESSION ({0} runs) ---".format(n_runs))
            for r in range(1, n_runs + 1):
                cached = load_run(out_dir, "with_session", r)
                if cached is not None:
                    print("  [resume] with_session run {0} already complete -> load".format(r))
                    with_runs.append(cached)
                    continue
                results = run_chain_once(chain, tool, "with_session", r, out_dir)
                save_run(out_dir, "with_session", r, results)
                with_runs.append(results)

        if include_gated:
            print("\n--- GATED_SESSION ({0} runs) ---".format(n_runs))
            for r in range(1, n_runs + 1):
                results = run_chain_once(chain, tool, "gated_session", r, out_dir)
                save_run(out_dir, "gated_session", r, results)
                gated_runs.append(results)

        # Native-resume arms (builtin / slm_native / stacked) all gate native
        # resume on USE_BUILTIN_SESSION=1, so set it for the arm and restore
        # after. Differ only in prompt prep (see run_chain_once):
        #   builtin    = RAW prompt        + native resume
        #   slm_native = SLM rewrite       + native resume   (handoff "STACKED")
        #   stacked    = SLM rewrite+bounded session + native resume (double mem)
        def _run_native_arm(variant_label: str, runs_acc: list) -> None:
            print("\n--- {0} (native {1} session) ({2} runs) ---".format(
                variant_label.upper(), tool, n_runs))
            prev = os.environ.get("USE_BUILTIN_SESSION")
            os.environ["USE_BUILTIN_SESSION"] = "1"
            try:
                for r in range(1, n_runs + 1):
                    cached = load_run(out_dir, variant_label, r)
                    if cached is not None:
                        print("  [resume] {0} run {1} already complete -> load".format(variant_label, r))
                        runs_acc.append(cached)
                        continue
                    results = run_chain_once(chain, tool, variant_label, r, out_dir)
                    save_run(out_dir, variant_label, r, results)
                    runs_acc.append(results)
            finally:
                if prev is None:
                    os.environ.pop("USE_BUILTIN_SESSION", None)
                else:
                    os.environ["USE_BUILTIN_SESSION"] = prev

        if include_builtin:
            _run_native_arm("builtin", builtin_runs)
        if include_slm_native:
            _run_native_arm("slm_native", slm_native_runs)
        if include_stacked:
            _run_native_arm("stacked", stacked_runs)
    except QuotaExhausted as e:
        quota_hit = True
        print("\n" + "!" * 72)
        print("  QUOTA EXHAUSTED — {0}".format(e))
        print("  Aborting cleanly. Only FULLY-completed arms are saved to the")
        print("  summary; the partial arm is discarded (no phantom 0-success).")
        print("!" * 72)

    no_agg = _agg_complete(no_runs) if no_runs is not None else None
    with_agg = _agg_complete(with_runs)
    gated_agg = _agg_complete(gated_runs) if include_gated else None
    builtin_agg = _agg_complete(builtin_runs) if include_builtin else None
    slm_native_agg = _agg_complete(slm_native_runs) if include_slm_native else None
    stacked_agg = _agg_complete(stacked_runs) if include_stacked else None
    save_summary(out_dir, no_agg, with_agg, chain["id"], tool, n_runs,
                 gated_agg=gated_agg, builtin_agg=builtin_agg,
                 slm_native_agg=slm_native_agg, stacked_agg=stacked_agg)
    if quota_hit:
        print("\n[saved] arms completed: " + ", ".join(
            name for name, agg in [("no", no_agg), ("with", with_agg),
                                    ("gated", gated_agg), ("builtin", builtin_agg),
                                    ("slm_native", slm_native_agg),
                                    ("stacked", stacked_agg)]
            if agg is not None) or "(none)")
    return no_agg, with_agg, gated_agg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", default="all", choices=["1", "2", "3", "4", "5", "all"])
    parser.add_argument("--tool", default="all", choices=["codex", "claude-code", "all"])
    parser.add_argument("--runs", type=int, default=3,
                        help="Number of runs per variant (default 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prepared prompts and expected files only")
    parser.add_argument("--reprint", action="store_true",
                        help="Re-display saved summaries without re-running")
    parser.add_argument(
        "--normalizer", default="slm",
        choices=["slm", "slm-anthropic", "slm-openai", "slm-openai-v2", "slm-subscription"],
        help="SLM normalizer backend (default: slm = auto-detect). "
             "Use slm-subscription to validate the Max-OAuth path.",
    )
    parser.add_argument(
        "--include-gated", action="store_true",
        help="Also run the GATED_SESSION arm (Haiku referential classifier "
             "decides per-turn whether to load session). Adds N runs of cost "
             "on top of NO_SESSION + WITH_SESSION.",
    )
    parser.add_argument(
        "--skip-no-session", action="store_true",
        help="Skip the NO_SESSION arm. Use when running a focused WITH-vs-GATED "
             "head-to-head; the existing NO_SESSION baseline at another out-dir "
             "is reused for comparison.",
    )
    parser.add_argument(
        "--include-builtin", action="store_true",
        help="Also run the BUILTIN arm — the downstream tool's NATIVE session "
             "(claude --resume / codex exec resume) with raw prompts. Measures "
             "PromptPilot's session against the tool's own conversation memory. "
             "Sets USE_BUILTIN_SESSION=1 for that arm automatically.",
    )
    parser.add_argument(
        "--skip-with-session", action="store_true",
        help="Skip the WITH_SESSION arm. Combine with --skip-no-session "
             "--include-builtin to run ONLY the native BUILTIN arm and reuse a "
             "prior WITH_SESSION result from another out-dir for the comparison "
             "(saves quota when WITH was already measured).",
    )
    parser.add_argument(
        "--include-slm-native", action="store_true",
        help="Also run the slm_native ISOLATION arm — SLM rewrite + the tool's "
             "NATIVE resume, with NO PromptPilot bounded session. This is the "
             "'STACKED' arm in the handoff/memory vocabulary: WITH_SESSION vs "
             "slm_native isolates the bounded-vs-native session mechanism while "
             "holding the SLM rewrite constant. Sets USE_BUILTIN_SESSION=1.",
    )
    parser.add_argument(
        "--include-stacked", action="store_true",
        help="Also run the stacked DOUBLE-MEMORY arm — SLM rewrite + PromptPilot "
             "bounded session + native resume layered together. Distinct from "
             "--include-slm-native. Sets USE_BUILTIN_SESSION=1.",
    )
    args = parser.parse_args()

    # Loud-fail guard for the missing-.env trap. We load .env from THIS file's
    # repo root (_REPO_ROOT/.env). Run from a git worktree with no .env, the
    # load silently no-ops; if a forced SDK normalizer (slm-anthropic /
    # slm-openai*) then finds no API key, every SLM call hits an auth error and
    # the harness silently falls back to the raw prompt -- invalidating the run
    # (observed 2026-05-20: 100 auth failures, 0 rewrites, an entire N=5 run
    # wasted). Only the forced SDK paths need a key here; 'slm' auto-detect and
    # 'slm-subscription' can use Max/ChatGPT OAuth, and create_normalizer()
    # already raises loudly if nothing resolves for those.
    _sdk_key_required = {
        "slm-anthropic": "ANTHROPIC_API_KEY",
        "slm-openai": "OPENAI_API_KEY",
        "slm-openai-v2": "OPENAI_API_KEY",
    }
    _need_key = _sdk_key_required.get(args.normalizer)
    if _need_key and not os.environ.get(_need_key):
        _env_path = _REPO_ROOT / ".env"
        raise SystemExit(
            "[chain_test_v2] --normalizer {0} requires {1}, but it is not set.\n"
            "  .env checked at: {2} (exists: {3})\n"
            "  Fix one of:\n"
            "    - copy .env.example to {2} and add {1}\n"
            "    - export {1} in your shell\n"
            "    - use --normalizer slm (auto-detect) or slm-subscription (OAuth, no key)\n"
            "  NOTE: running from a git worktree? .env is NOT copied into worktrees --\n"
            "  that is the trap that silently disables the SLM and sends raw prompts "
            "downstream.".format(args.normalizer, _need_key, _env_path, _env_path.exists())
        )

    # Thread the chosen normalizer through to _make_normalizer() and
    # _is_referential() via module-level state.
    global _NORMALIZER_NAME
    _NORMALIZER_NAME = args.normalizer
    print(f"[startup] normalizer={_NORMALIZER_NAME}")

    targets = CHAINS if args.chain == "all" else [
        c for c in CHAINS if c["id"] == "chain{0}".format(args.chain)
    ]
    tools = ["codex", "claude-code"] if args.tool == "all" else [args.tool]

    # Reap any orphaned claude.exe processes from prior killed/crashed runs.
    # They compete for the API key's concurrent-request quota and cause new
    # calls to stall in silent rate-limit backoff.
    if "claude-code" in tools:
        killed = reap_claude_orphans()
        if killed:
            print("[startup] reaped {0} orphaned claude.exe processes".format(killed))

    for chain in targets:
        if args.dry_run:
            dry_run_chain(chain, tools[0])
            continue

        for tool in tools:
            out_dir = OUT_DIR / tool / chain["id"]
            if args.reprint:
                summary = load_summary(out_dir)
                if not summary:
                    print("  [skip] no saved summary for {0}/{1}".format(chain["id"], tool))
                    continue
                print_chain_summary(chain, summary["no_session_agg"],
                                    summary["with_session_agg"], tool,
                                    summary["n_runs"],
                                    gated_agg=summary.get("gated_session_agg"))
                continue

            print("\n" + "=" * 60)
            print("Running {0} with {1} ({2} runs){3}{4}{5}{6}{7}{8}".format(
                chain["id"], tool, args.runs,
                " [+gated]" if args.include_gated else "",
                " [+builtin]" if args.include_builtin else "",
                " [+slm_native]" if args.include_slm_native else "",
                " [+stacked]" if args.include_stacked else "",
                " [no-NO]" if args.skip_no_session else "",
                " [no-WITH]" if args.skip_with_session else ""))
            no_agg, with_agg, gated_agg = run_chain_full(
                chain, tool, args.runs,
                include_gated=args.include_gated,
                skip_no_session=args.skip_no_session,
                include_builtin=args.include_builtin,
                skip_with_session=args.skip_with_session,
                include_slm_native=args.include_slm_native,
                include_stacked=args.include_stacked,
            )
            if with_agg is None:
                # Quota abort before WITH_SESSION completed — nothing to chart.
                # run_chain_full already printed the abort + saved arms message.
                print("(WITH_SESSION arm did not complete; skipping summary table. "
                      "See saved per-run files + summary.json for any complete arms.)")
            elif no_agg is None:
                # Synthesize a zero-baseline "NO" agg for print_chain_summary so
                # the existing diff table renders; cells will show 0s/n/a but
                # the WITH and GATED columns are correct.
                placeholder = [
                    {**t, "success_mean": 0.0, "bailed_rate": 0.0,
                     "tool_calls_mean": 0.0, "tool_calls_stdev": 0.0,
                     "input_tokens_mean": 0.0, "output_tokens_mean": 0.0,
                     "output_tokens_stdev": 0.0, "wall_t_mean": 0.0}
                    for t in with_agg
                ]
                print("(NO_SESSION arm skipped; columns labeled NO read as 0 below)")
                print_chain_summary(chain, placeholder, with_agg, tool, args.runs,
                                    gated_agg=gated_agg)
            else:
                print_chain_summary(chain, no_agg, with_agg, tool, args.runs,
                                    gated_agg=gated_agg)

    print("\nOutput files:", OUT_DIR)


if __name__ == "__main__":
    main()
