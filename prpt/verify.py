"""Verify-gate (OPTIMIZATION_LEVERS Lever #1).

prpt is otherwise open-loop: shape the prompt, fire the agent, trust the output. This
closes the loop with a GROUND-TRUTH signal — after the downstream agent edits, run the
target repo's test runner scoped to the changed files. On failure the caller can feed the
specific, compressed failure back as one targeted follow-up turn (retry-capped).

Design constraints (from the re-test + the L1 critique):
  * Deterministic exit-code signal — NO SLM in the loop, so the signal itself costs 0 tokens
    and can't hallucinate a pass/fail. (Distinct from the scrapped SLM-opinion evaluator.)
  * SAFETY: only ever run an ALLOW-LISTED command template keyed by the detected
    test_framework. We never execute an arbitrary discovered script, and test targets are
    validated to be existing files under cwd (and never flag-like) before being passed.
  * Compliance-neutral: this runs the *repo's own* test runner locally; it touches no model,
    no OAuth token, no network.

This module is pure of prpt's run flow — cli.py wires it in around adapter.run(); the harness
reuses run_verify() as one half of the end-state verifier (lever 6e).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from prpt.core.types import RepoMetadata
from prpt.repo.loader import find_test_pair

# Bounds (override via env for the harness / slow suites).
VERIFY_TIMEOUT_S: int = int(os.environ.get("PROMPTPILOT_VERIFY_TIMEOUT", "300"))
VERIFY_MAX_RETRIES: int = int(os.environ.get("PROMPTPILOT_VERIFY_RETRIES", "1"))
_OUTPUT_TAIL_LINES = 30

# Routes that produce no code edits -> nothing to verify. NB: this is keyed on INTENT,
# not scope. A BROAD refactor is an `act` turn and IS verified (high blast radius is exactly
# where verification matters); only answer/explain/clarify/passthrough are skipped.
_NON_EDITING_ROUTES = {"answer", "clarify", "passthrough"}


def should_verify(route: Optional[str], requested: bool) -> bool:
    """Gate decision: verify only when explicitly requested AND the turn edits code.

    Unknown/None route -> verify (conservative; route defaults to "act" upstream).
    """
    return bool(requested) and route not in _NON_EDITING_ROUTES


@dataclass
class VerifyResult:
    """Outcome of one verify run. `passed` is meaningful only when `ran` is True."""
    ran: bool
    passed: bool = False
    command: Optional[List[str]] = None
    returncode: Optional[int] = None
    output_tail: str = ""
    skipped_reason: Optional[str] = None
    duration_s: float = 0.0
    targets: List[str] = field(default_factory=list)

    def to_log(self) -> dict:
        """Compact, JSON-safe view for the run log (no full output)."""
        return {
            "ran": self.ran,
            "passed": self.passed,
            "returncode": self.returncode,
            "command": " ".join(self.command) if self.command else None,
            "targets": self.targets,
            "skipped_reason": self.skipped_reason,
            "duration_s": round(self.duration_s, 2),
        }


def _is_test_file(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    parts = {p.lower() for p in Path(rel_path).parts}
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js") or name.endswith(".test.ts")
        or name.endswith(".spec.js") or name.endswith(".spec.ts")
        or "tests" in parts or "test" in parts or "spec" in parts
    )


def _safe_target(rel_path: str, cwd: str) -> bool:
    """A target must be an existing file under cwd and not flag-like (no leading '-')."""
    if not rel_path or rel_path.startswith("-"):
        return False
    try:
        full = (Path(cwd) / rel_path).resolve()
        return full.is_file() and str(full).startswith(str(Path(cwd).resolve()))
    except Exception:
        return False


def discover_verify_targets(repo: RepoMetadata, changed_files: List[str]) -> List[str]:
    """Map changed files -> the test files that exercise them.

    A changed test file is its own target; a changed source file contributes its
    test-pair (via the shared find_test_pair). Validated + de-duplicated.
    """
    targets: List[str] = []
    for f in changed_files or []:
        if _is_test_file(f):
            cand = f
        else:
            cand = find_test_pair(f, repo.cwd)
        if cand and _safe_target(cand, repo.cwd) and cand not in targets:
            targets.append(cand)
    return targets


def build_verify_command(framework: Optional[str], targets: List[str],
                         full_suite: bool) -> Optional[List[str]]:
    """Allow-listed command template per framework, or None if unsupported/no-target.

    Only these frameworks are runnable; everything else is skipped (never guessed).
    """
    if not framework:
        return None
    fw = framework.lower()
    if not targets and not full_suite:
        return None  # nothing scoped to verify; caller records skip
    if fw == "pytest":
        # -x (fail-fast): the gate only needs pass/fail, so stop at the first failure —
        # faster red path AND a cleaner single-failure retry prompt.
        base = [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
        return base + (targets if not full_suite else [])
    if fw in ("jest", "vitest"):
        # `npx <runner> --run <targets>` (vitest) / `npx jest <targets>` — best-effort.
        run_flag = ["--run"] if fw == "vitest" else []
        return ["npx", fw] + run_flag + (targets if not full_suite else [])
    if fw == "mocha":
        return ["npx", "mocha"] + (targets if not full_suite else [])
    return None  # xunit/nunit/junit (.NET/Java) — out of scope for the first cut


def _compress(stdout: str, stderr: str) -> str:
    blob = (stdout or "") + ("\n" + stderr if stderr else "")
    lines = [ln for ln in blob.splitlines() if ln.strip()]
    return "\n".join(lines[-_OUTPUT_TAIL_LINES:])


def run_verify(repo: RepoMetadata, changed_files: List[str], *,
               timeout_s: int = VERIFY_TIMEOUT_S, full_suite: bool = False) -> VerifyResult:
    """Run the allow-listed verify command for `repo`, scoped to `changed_files`.

    Returns a VerifyResult; `ran=False` (with skipped_reason) when there is no supported
    framework or nothing to scope — a skip is NOT a failure (the gate only retries on a real
    red run, never on "couldn't verify").
    """
    targets = discover_verify_targets(repo, changed_files)
    cmd = build_verify_command(repo.test_framework, targets, full_suite)
    if cmd is None:
        reason = ("no test_framework detected" if not repo.test_framework
                  else "framework '{0}' not supported".format(repo.test_framework)
                  if repo.test_framework.lower() not in ("pytest", "jest", "vitest", "mocha")
                  else "no changed test targets to scope (use full_suite to run all)")
        return VerifyResult(ran=False, skipped_reason=reason, targets=targets)

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=repo.cwd, capture_output=True, text=True,
                              timeout=timeout_s)
        rc = proc.returncode
        tail = _compress(proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        rc, tail = 124, "VERIFY_TIMEOUT after {0}s".format(timeout_s)
    except FileNotFoundError as e:  # runner not installed (e.g. npx absent)
        return VerifyResult(ran=False, command=cmd, targets=targets,
                            skipped_reason="runner unavailable: {0}".format(e))
    dur = time.time() - t0
    # pytest exit 5 = "no tests collected" — treat as a skip, not a failure.
    if repo.test_framework and repo.test_framework.lower() == "pytest" and rc == 5:
        return VerifyResult(ran=False, command=cmd, returncode=5, targets=targets,
                            output_tail=tail, duration_s=dur,
                            skipped_reason="no tests collected")
    return VerifyResult(ran=True, passed=(rc == 0), command=cmd, returncode=rc,
                        output_tail=tail, duration_s=dur, targets=targets)


def run_gate(adapter, args, repo: RepoMetadata, *, retries: Optional[int] = None,
             full_suite: bool = False, log=None) -> "tuple[VerifyResult, Optional[int]]":
    """Verify the agent's edits; on a REAL failure, drive up to `retries` targeted,
    SLM-bypassed retries by re-invoking adapter.run(build_retry_prompt(...)) against the
    same working tree. Returns (final_result, last_retry_exit_code).

    last_retry_exit_code is None when no retry ran (caller keeps the original exit code).
    A SKIP (no framework / nothing to scope / no tests collected) is never a failure and
    never triggers a retry — the gate retries only on a deterministically red run.
    `adapter` must expose .run(prompt, args) and .last_modified_files.
    """
    emit = log or (lambda _m: None)
    modified = getattr(adapter, "last_modified_files", None) or []
    result = run_verify(repo, modified, full_suite=full_suite)
    left = VERIFY_MAX_RETRIES if retries is None else retries
    last_exit: Optional[int] = None
    attempt = 0
    while result.ran and not result.passed and left > 0:
        attempt += 1
        emit("[promptpilot] verify FAILED (rc={0}); targeted retry {1} (SLM-bypassed)...".format(
            result.returncode, attempt))
        last_exit = adapter.run(build_retry_prompt(result, modified), args)
        modified = getattr(adapter, "last_modified_files", None) or []
        result = run_verify(repo, modified, full_suite=full_suite)
        left -= 1
    if result.ran:
        emit("[promptpilot] verify: {0}".format(
            "PASS" if result.passed else "FAIL (retries exhausted)"))
    elif result.skipped_reason:
        emit("[promptpilot] verify skipped: {0}".format(result.skipped_reason))
    return result, last_exit


def resolve_exit_code(agent_exit: Optional[int], retry_exit: Optional[int],
                      result: Optional[VerifyResult]) -> int:
    """Final CLI exit code after the verify-gate.

    A FAILED verification (ran and not passed) is authoritative: surface it as nonzero so
    CI / scripts / the measurement harness can gate on the verify result, even when the
    agent's own process exited 0 (it ran fine, it just didn't make the tests pass). A SKIP
    (ran is False) or a PASS leaves the code as the agent's / last-retry's exit.
    """
    code = retry_exit if retry_exit is not None else (agent_exit or 0)
    if result is not None and result.ran and not result.passed:
        return result.returncode or 1
    return code


def build_retry_prompt(result: VerifyResult, changed_files: List[str]) -> str:
    """A targeted, SLM-free follow-up turn quoting the specific failure.

    Deliberately preserves scope (fix the failure, don't expand) — multi-turn scope
    expansion was a measured failure mode (OPTIMIZATION_LEVERS 6c).
    """
    files = ", ".join(changed_files) if changed_files else "(none reported)"
    cmd = " ".join(result.command) if result.command else "(verify command)"
    return (
        "The change you just made did not pass verification.\n\n"
        "Verification command: {cmd}\n"
        "Exit code: {rc}\n"
        "Output (tail):\n{tail}\n\n"
        "Files you changed: {files}\n\n"
        "Fix the failure. Make the SMALLEST change that makes the verification pass; "
        "do not revert unrelated work or broaden the scope of the task. If a test is "
        "genuinely wrong, correct the code first and explain why."
    ).format(cmd=cmd, rc=result.returncode, tail=result.output_tail or "(no output)",
             files=files)
