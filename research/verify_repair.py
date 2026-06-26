r"""Stage-2 verify-and-repair mechanism (docs/SESSION_MEMORY_VERIFY_REPAIR.md, v2).

WHY (the N=10 / run9 finding): the additive-bias guard eliminates the destructive-DROP tax
(0/10 on-target) but cannot catch "additive-but-buggy" — a contract whose public name is KEPT
yet whose behavior breaks at runtime (run9: a merge bug crashed the kept connect_timeout
override). A diff/substring check MISSES that (zero removed lines). Only RUNNING the contract
catches it. So this module is execution-based:

  * a contract carries an executable PROBE that ISSUES a request (MockTransport) and asserts
    behavior -- not merely constructs/inspects a signature (the driving-decision corollary);
  * the SLM may GENERATE a probe but NEVER judges it -- the subprocess exit code is the verdict
    (non-circular, ~0 model tokens to run);
  * a probe is stored only if it passes a POSITIVE control (passes on the tree that implemented
    the contract) AND a NEGATIVE control (FAILS on the clean baseline where the contract does not
    exist) -- this rejects vacuously-passing probes (the "false-negative probe" hole all three
    v1 reviews flagged);
  * outcomes are clean | violated | unknown, with `unknown` FAIL-CLOSED: never `violated` (no
    false alarms) and never silently `clean` (it censors a clean claim);
  * on the FINAL turn ALL probes run (overlap filter bypassed) -- cross-cutting breakage is the
    overlap filter's blind spot and the consolidation turn is where it bites;
  * repair is ONE gated turn; afterwards the tree is RECONCILED -- re-verify, accept if clean,
    else git/fs ROLLBACK so the system never ships a net-negative edit.

Run a self-smoke (no model, no network):  python research/verify_repair.py
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from memory_ledger import _mentions   # reuse the guard's word-boundary matcher (single definition)

PROBE_TIMEOUT_S = 30                 # per-probe wall cap (kills hangs -> unknown, never violated)
# Imports a probe may use. A probe needing anything else is rejected at birth (sandbox, S6.E).
_ALLOWED_PROBE_IMPORTS = {"httpx", "lib", "json", "math", "datetime", "types", "typing", "decimal"}
# Builtins / names whose mere reference is an escape vector -> reject (AST-checked, NOT substring:
# a variable named `socket` or the token `open(` in a comment must not trip this, while
# getattr(__builtins__, 'open') must).
_BANNED_NAMES = {"__import__", "__builtins__", "eval", "exec", "compile", "open", "getattr",
                 "setattr", "delattr", "globals", "vars", "input", "breakpoint", "memoryview"}
# Dunder attributes that bridge to builtins/types (the classic sandbox-escape chain). Kept for
# documentation; _is_dunder() below blanket-bans ALL dunders, which is what actually closes the
# `print.__self__.open(...)` style escape (any builtin function's __self__ is the builtins module).
_BANNED_ATTRS = {"__globals__", "__builtins__", "__class__", "__bases__", "__subclasses__",
                 "__mro__", "__dict__", "__import__", "__loader__", "__code__", "__closure__",
                 "__getattribute__", "__getattr__", "__self__", "__func__", "__module__"}


def _is_dunder(name: str) -> bool:
    """A `__dunder__` name/attribute. No legitimate probe needs one, and every known sandbox escape
    (`__self__`/`__globals__`/`__class__`/...) is a dunder -> blanket-ban them rather than enumerate."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


# ---------------------------------------------------------------------------
# Probe sandbox (S6.E) -- a static guard; probes are SLM-generated code.
# ---------------------------------------------------------------------------
def sandbox_check(probe_src: str) -> tuple[bool, str | None]:
    """Static safety/relevance gate for a probe. Returns (ok, reason_if_not). AST-based (NOT a
    substring scan): inspects imports + dangerous NAME references (open/getattr/__builtins__/...) +
    dunder ATTRIBUTE access, so a variable named `socket` or the token `open(` inside a comment or
    string is NOT falsely rejected, while `getattr(__builtins__, 'open')` and
    `().__class__.__bases__` ARE caught. MockTransport handles the network; this bounds fs/process/
    dynamic-exec. Not a perfect sandbox -- a cheap reject of the obviously-unsafe before we execute
    SLM-emitted code (research harness; runtime containment is the subprocess + timeout)."""
    try:
        tree = ast.parse(probe_src)
    except SyntaxError as e:
        return False, "syntax-error: {0}".format(e)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _ALLOWED_PROBE_IMPORTS:
                    return False, "disallowed import: {0}".format(a.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_PROBE_IMPORTS:
                return False, "disallowed import-from: {0}".format(node.module)
        elif isinstance(node, ast.Name) and (node.id in _BANNED_NAMES or _is_dunder(node.id)):
            return False, "disallowed name: {0}".format(node.id)
        elif isinstance(node, ast.Attribute) and (node.attr in _BANNED_ATTRS or _is_dunder(node.attr)):
            # blanket dunder ban closes attribute-chain escapes like print.__self__.open(...)
            return False, "disallowed attribute: {0}".format(node.attr)
    return True, None


# ---------------------------------------------------------------------------
# Execution + verdict classification
# ---------------------------------------------------------------------------
def _file_line(stderr: str) -> str | None:
    m = re.findall(r'File "([^"]+)", line (\d+)', stderr or "")
    return "{0}:{1}".format(Path(m[-1][0]).name, m[-1][1]) if m else None


def _last_exc_line(stderr: str) -> str:
    lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# Exception types that mean the probe/tree could not LOAD or the source was malformed -- NOT a
# contract failure -> unknown (fail-closed). Import-breaking migrations are conservatively `unknown`.
_INFRA_EXC = {"ModuleNotFoundError", "ImportError", "SyntaxError", "IndentationError"}


def _is_violation_exc(exc_type: str) -> bool:
    """True iff `exc_type` is a genuine runtime contract failure (AssertionError, TypeError, ...).
    Decided on the EXCEPTION TYPE at the start of the last traceback line -- NOT a substring of the
    whole stderr (a violation whose message merely mentions 'ImportError' must still count as a
    violation). Infra/load errors and non-exception abnormal exits are NOT violations."""
    if not exc_type or exc_type in _INFRA_EXC:
        return False
    return exc_type.endswith(("Error", "Exception"))


def _classify(rc: int, stderr: str, timed_out: bool) -> tuple[str, dict | None]:
    """Map a probe subprocess result to (status, evidence). FAIL-CLOSED in BOTH directions: a real
    contract exception -> `violated`; everything else non-zero (load/import/syntax error, an
    abnormal non-exception exit such as sys.exit, or empty stderr) -> `unknown`, never a false
    `violated`."""
    if timed_out:
        return "unknown", {"error": "timeout", "file_line": None}
    if rc == 0:
        return "clean", None
    exc = _last_exc_line(stderr)
    exc_type = exc.split(":", 1)[0].strip()
    if _is_violation_exc(exc_type):
        return "violated", {"error": exc, "file_line": _file_line(stderr)}
    return "unknown", {"error": exc or "non-zero exit (no python exception)",
                       "file_line": _file_line(stderr)}


def run_probe(probe_src: str, cwd: str | Path, timeout_s: int = PROBE_TIMEOUT_S) -> tuple[str, dict | None]:
    """Execute a probe against the tree at `cwd` in an isolated subprocess. Returns
    (status, evidence) where status in {clean, violated, unknown}. The probe imports the target
    module from cwd (sys.path[0]='' under `python -c`), issues its request, and raises on
    violation. Non-circular: the exit code is the judge, no model in the loop."""
    ok, reason = sandbox_check(probe_src)
    if not ok:
        return "unknown", {"error": "sandbox-reject: {0}".format(reason), "file_line": None}
    try:
        p = subprocess.run([sys.executable, "-c", probe_src], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return _classify(124, "", True)
    except OSError as e:
        return "unknown", {"error": "infra: {0}".format(e), "file_line": None}
    return _classify(p.returncode, p.stderr, False)


# ---------------------------------------------------------------------------
# Birth validation: positive + NEGATIVE control (the anti-vacuous-probe gate)
# ---------------------------------------------------------------------------
def validate_probe_at_birth(probe_src: str, impl_cwd: str | Path, baseline_cwd: str | Path,
                            timeout_s: int = PROBE_TIMEOUT_S) -> tuple[bool, dict]:
    """A probe is a real regression detector only if it PASSES on the tree that implemented the
    contract AND is VIOLATED on the baseline where the contract behavior does not exist. We require
    `violated` (not merely 'not clean') on the baseline so a vacuous probe -- one that asserts
    something always true, or imports a missing symbol and merely errors -- is rejected: it never
    *actively detects* the absent behavior. NOTE: `baseline_cwd` must be a SURFACE-PRESENT tree (the
    public name exists but the behavior is absent, e.g. vanilla httpx at d764bfc where
    `client.get(connect_timeout=...)` raises) -- a too-bare baseline yields `unknown` and the probe
    is conservatively rejected.

    LIMITATION (review #8): this proves the probe discriminates SOME difference between impl and
    baseline, not necessarily the NAMED contract -- a probe carrying an UNRELATED assertion that is
    coincidentally false on the baseline can pass both controls. Mitigated by the single-assertion
    generation instruction (PROBE_GEN_INSTR); a per-contract targeted-mutation control (break only
    the contract, require the probe to fail) is future work. Returns (ok, detail)."""
    sb_ok, sb_reason = sandbox_check(probe_src)
    if not sb_ok:
        return False, {"reason": "sandbox", "detail": sb_reason}
    pos, _ = run_probe(probe_src, impl_cwd, timeout_s)
    neg, _ = run_probe(probe_src, baseline_cwd, timeout_s)
    positive_ok = (pos == "clean")                 # must pass where the contract exists
    negative_ok = (neg == "violated")              # must ACTIVELY DETECT the absent behavior (not just error)
    return (positive_ok and negative_ok), {
        "positive": pos, "negative": neg,
        "positive_ok": positive_ok, "negative_ok": negative_ok,
        "reason": None if (positive_ok and negative_ok)
                  else ("not-passing-on-impl" if not positive_ok else "vacuous-passes-on-baseline"),
    }


# ---------------------------------------------------------------------------
# The verifier (instrument-first detector)
# ---------------------------------------------------------------------------
def _changed_text(cwd: str | Path, changed_files) -> str:
    """Lowercased content of the changed files (best-effort) so the verifier can flag a contract
    whose SYMBOL was touched even when its recorded file set does not intersect changed_files --
    matching memory_ledger.guard_hits' files-OR-symbols recall (S6.B)."""
    parts = []
    for f in (changed_files or []):
        try:
            parts.append(Path(cwd, f).read_text(encoding="utf-8", errors="replace").lower())
        except OSError:
            continue
    return "\n".join(parts)


def _at_risk(contract: dict, changed_files, changed_text: str = "") -> bool:
    """At risk iff the contract's files overlap the changed files OR any of its symbols is
    word-boundary-mentioned in the changed files' content -- the SAME files-OR-symbols depth as
    memory_ledger.guard_hits, not files-only (a symbol moved within an un-recorded file would
    otherwise escape per-turn verification until the final turn)."""
    if set(changed_files or []) & set(contract.get("files") or []):
        return True
    return any(_mentions(s, changed_text) for s in (contract.get("symbols") or []))


def verify_contracts(contracts: dict, cwd: str | Path, changed_files,
                     final: bool = False, timeout_s: int = PROBE_TIMEOUT_S) -> dict:
    """Run the probe (or fall back to `tests`) for every AT-RISK contract against the post-turn
    tree. On the FINAL turn, bypass the at-risk filter and check ALL contracts (overlap-recall
    ceiling, S6.B). Returns a VerifierResult dict (see docs S5). FAIL-CLOSED: a contract with no
    runnable probe AND no usable test -> `unknown` (never silently clean)."""
    changed_text = "" if final else _changed_text(cwd, changed_files)
    checks = []
    for feat, c in (contracts or {}).items():
        if not (final or _at_risk(c, changed_files, changed_text)):
            continue
        probe = c.get("probe")
        if probe:
            status, evidence = run_probe(probe, cwd, timeout_s)
            via = "probe"
        elif c.get("tests"):
            # fallback: a scoped, no-socket existing test (caller is responsible for socket-free -k)
            status, evidence = _run_tests(c["tests"], cwd, timeout_s)
            via = "tests"
        else:
            status, evidence = "unknown", {"error": "no probe and no tests", "file_line": None}
            via = "none"
        checks.append({"feature": feat, "status": status, "evidence": evidence, "via": via})
    return {"checks": checks, "all_probes_run": bool(final),
            "violations": [c for c in checks if c["status"] == "violated"]}


def _run_tests(test_ids, cwd: str | Path, timeout_s: int) -> tuple[str, dict | None]:
    """Fallback when a contract has no probe: run its specific test ids (scoped, no full suite).
    Same fail-closed mapping as run_probe (rc 5 = no-match -> unknown, not clean)."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", *list(test_ids), "-q", "--no-header",
                            "-p", "no:cacheprovider"], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return "unknown", {"error": "timeout", "file_line": None}
    except OSError as e:
        return "unknown", {"error": "infra: {0}".format(e), "file_line": None}
    rc = p.returncode
    if rc == 0:
        return "clean", None
    if rc == 1:                                      # genuine test FAILURES = contract violated
        return "violated", {"error": _last_exc_line(p.stdout + p.stderr), "file_line": None}
    # rc 5 (no match) / 2 (interrupted/collection-error) / 3 (internal) / 4 (usage) = the contract
    # was NOT evaluated -> unknown (fail-closed), not a violation.
    return "unknown", {"error": "pytest rc={0} (contract not evaluated)".format(rc), "file_line": None}


def run_tax_status(vr: dict) -> str:
    """Per-run accounting (docs S8, pre-registered). `unknown` is NOT clean -- it censors a clean
    claim. Returns: 'taxed' (>=1 violated), 'unverified' (no violated but >=1 unknown),
    'clean' (all checked contracts clean), or 'none' (nothing was at risk)."""
    statuses = [c["status"] for c in vr.get("checks", [])]
    if not statuses:
        return "none"
    if any(s == "violated" for s in statuses):
        return "taxed"
    if any(s == "unknown" for s in statuses):
        return "unverified"
    return "clean"


# ---------------------------------------------------------------------------
# Repair + reconciliation (S6.D) -- one gated turn, then accept or ROLL BACK
# ---------------------------------------------------------------------------
_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")


def _chmod_retry(func, path, _exc):
    """shutil.rmtree onerror hook: clear the read-only bit and retry (Windows read-only files
    abort rmtree otherwise, leaving a half-restored tree)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _remove_child(child: Path) -> None:
    """Remove one top-level child of the tree. A real directory is rmtree'd (read-only-tolerant);
    a FILE or a SYMLINK -- including a symlink-to-a-directory -- is unlinked WITHOUT following it,
    so a repair-added symlink can never delete its target's contents OUTSIDE the tree."""
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child, onerror=_chmod_retry)
        return
    try:
        child.unlink()
    except OSError:
        try:
            os.chmod(child, stat.S_IWRITE)
            child.unlink()
        except OSError:
            pass


def _snapshot_tree(cwd: str | Path) -> str:
    """Copy the working tree (excluding .git) so a failed repair can be rolled back to the
    PRE-repair state (which already contains this turn's edits) -- git checkout/clean cannot do
    that (it would go to HEAD). symlinks=True + ignore_dangling_symlinks=True so a dangling/looping
    symlink does not crash the snapshot before repair even runs. For very large repos a
    `git stash create` ref could replace this."""
    snap = tempfile.mkdtemp(prefix="repair_snap_")
    shutil.copytree(str(cwd), snap, dirs_exist_ok=True, ignore=_IGNORE,
                    symlinks=True, ignore_dangling_symlinks=True)
    return snap


def _restore_tree(cwd: str | Path, snap: str) -> None:
    """Restore the working tree to the snapshot: drop every child (except .git) symlink-safely and
    read-only-tolerantly, then copy the snapshot back -- so files the repair ADDED are removed and
    edits reverted, without following symlinks or choking on read-only files."""
    cwd = Path(cwd)
    for child in cwd.iterdir():
        if child.name == ".git":
            continue
        _remove_child(child)
    shutil.copytree(snap, str(cwd), dirs_exist_ok=True, ignore=_IGNORE,
                    symlinks=True, ignore_dangling_symlinks=True)


def repair_and_reconcile(cwd: str | Path, repair_fn, verify_fn) -> dict:
    """ONE gated repair turn, then reconcile. `repair_fn(cwd)` applies the fix (prod: fire a codex
    repair turn; tests: an edit). `verify_fn(cwd) -> bool` re-verifies (True = clean). If clean,
    ACCEPT the repaired tree (caller refreshes ledger/endstate). If still violated, ROLL BACK to
    the pre-repair tree so we never ship a net-negative edit. No loop (cap = 1).
    Returns {outcome: repaired|unrepaired, rolled_back: bool}."""
    snap = _snapshot_tree(cwd)
    try:
        repair_fn(cwd)
        if verify_fn(cwd):
            return {"outcome": "repaired", "rolled_back": False}
        _restore_tree(cwd, snap)
        return {"outcome": "unrepaired", "rolled_back": True}
    finally:
        shutil.rmtree(snap, ignore_errors=True)


# ---------------------------------------------------------------------------
# Harness hook (docs S7) -- the end-to-end verify-then-(gated)-repair step a run-chain
# turn invokes. Pure orchestration: the ONLY model call is inside `repair_runner`.
# ---------------------------------------------------------------------------
def verify_and_maybe_repair(prior_contracts: dict, cwd: str | Path, changed_files, final: bool,
                            repair_runner, timeout_s: int = PROBE_TIMEOUT_S) -> dict:
    """Verify the at-risk prior contracts against the post-turn tree; on a (reproduced, hence
    high-confidence) violation, fire ONE gated repair via `repair_runner(cwd, violations)` and
    reconcile -- accept the repaired tree or roll back. `repair_runner` is injected (prod: a codex
    repair turn; tests: a stub) so this is unit-testable with no model. Returns a RunVerifierMetrics
    dict (docs §5/§8)."""
    vr = verify_contracts(prior_contracts, cwd, changed_files, final=final, timeout_s=timeout_s)
    m = {"checked": len(vr["checks"]),
         "clean": sum(1 for c in vr["checks"] if c["status"] == "clean"),
         "violated": len(vr["violations"]),
         "unknown": sum(1 for c in vr["checks"] if c["status"] == "unknown"),
         "status": run_tax_status(vr),
         "repair_fired": False, "repair_outcome": "na", "rolled_back": False,
         "violations": [{"feature": c["feature"], "evidence": c.get("evidence")}
                        for c in vr["violations"]]}
    if vr["violations"]:        # every violation is a reproduced execution failure => high-confidence
        m["repair_fired"] = True
        res = repair_and_reconcile(
            cwd,
            repair_fn=lambda d: repair_runner(d, vr["violations"]),
            verify_fn=lambda d: not verify_contracts(prior_contracts, d, changed_files,
                                                     final=final, timeout_s=timeout_s)["violations"])
        m["repair_outcome"] = res["outcome"]
        m["rolled_back"] = res["rolled_back"]
        m["status_after"] = "clean" if res["outcome"] == "repaired" else m["status"]
    return m


# ---------------------------------------------------------------------------
# Probe generation (SLM) -- wired, injectable; the ONLY model cost. The SLM
# GENERATES; it never JUDGES (run_probe does). Birth-validated before storage.
# ---------------------------------------------------------------------------
PROBE_GEN_INSTR = (
    "You write a tiny Python PROBE that verifies one durable contract still works AT RUNTIME. "
    "HARD RULES: self-contained; import only httpx + stdlib; use httpx.MockTransport (NO network); "
    "ISSUE A REQUEST via client.get(...)/client.request(...) to exercise the real path (do NOT "
    "merely build_request or inspect a signature); use a SINGLE assertion that targets ONLY the "
    "named contract (an unrelated assertion would let the probe pass birth-validation on a "
    "difference that is not the contract); raise on violation (a bare assert is fine); no "
    "file/process/eval side effects. Output ONLY the python."
)


def _judge_text(out) -> str | None:
    """Extract the text from a judge return, tolerating protocol-shape differences: the project
    Judge `(text, cost, walltime)` tuple, a bare string, or any `(text, ...)` sequence. An unknown
    shape -> None (the caller records a probe-generation MISS; never a silent crash)."""
    if isinstance(out, str):
        return out
    if isinstance(out, (tuple, list)) and out and isinstance(out[0], str):
        return out[0]
    return None


def generate_probe(contract: dict, judge=None) -> str | None:
    """Ask the SLM for a probe exercising `contract`. Returns probe source, or None -- and None is a
    RECORDED failure (callers count probes_failed_to_generate, docs §5), NOT a silent gap. Tolerant
    of judge protocol shape: a callable returning `(text, cost, walltime)` (the project Judge
    protocol), a callable returning a bare string, or an object exposing `.complete(prompt)`. Caller
    MUST birth-validate (positive+negative) before storing -- generation is not trusted."""
    if judge is None:
        try:
            from prpt.judges import get_default_judge
            judge = get_default_judge()
        except Exception:
            return None
    if judge is None:
        return None
    prompt = (PROBE_GEN_INSTR + "\n\n[Contract]\n"
              + "feature: {0}\ncontract: {1}\nsymbols: {2}\n".format(
                  contract.get("feature", ""), contract.get("contract", ""),
                  ", ".join(map(str, contract.get("symbols") or []))))
    try:
        try:
            out = judge(prompt)                  # project Judge protocol: callable -> (text, ...)
        except TypeError:
            out = judge.complete(prompt) if hasattr(judge, "complete") else None
        src = _strip_code_fence(_judge_text(out) or "")
    except Exception:
        return None
    return src or None


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


# ---------------------------------------------------------------------------
# Self-smoke (no model, no network): the run9-class fixture proves the premise.
# ---------------------------------------------------------------------------
_R9_BEFORE = (
    "def _merge(extensions, connect_timeout):\n"
    "    base = dict(extensions)\n"
    "    if connect_timeout is not None:\n"
    "        base['timeout'] = {'connect': connect_timeout}\n"
    "    return base\n"
    "def get(connect_timeout=None, extensions=None):\n"
    "    return _merge(extensions or {'timeout': {'connect': 5.0}}, connect_timeout)\n"
)
_R9_AFTER = (                                   # signature KEPT, behavior BROKEN (run9 bug)
    "def _merge(extensions, connect_timeout):\n"
    "    t = {'connect': connect_timeout} if connect_timeout is not None else None\n"
    "    if t is not None:\n"
    "        return dict(**extensions, timeout=t)\n"   # duplicate 'timeout' -> TypeError
    "    return dict(extensions)\n"
    "def get(connect_timeout=None, extensions=None):\n"
    "    return _merge(extensions or {'timeout': {'connect': 5.0}}, connect_timeout)\n"
)
_R9_PROBE = (                                   # EXERCISES the path (calls get), not just signature
    "from lib import get\n"
    "ext = get(connect_timeout=0.5)\n"
    "assert ext['timeout']['connect'] == 0.5\n"
)
_VACUOUS_PROBE = "from lib import get\nassert True\n"   # passes on anything -> must be rejected at birth


def _write_tree(src: str) -> str:
    d = tempfile.mkdtemp(prefix="vr_smoke_")
    (Path(d) / "lib.py").write_text(src, encoding="utf-8")
    return d


if __name__ == "__main__":
    # Quick manual demo of the run9-class premise (execution probe catches a kept-but-broken tax
    # that a diff/removed-symbol check would miss). FULL coverage — birth controls, fail-closed
    # unknown, tax accounting, repair/rollback, sandbox — lives in the pytest suite below.
    before, after = _write_tree(_R9_BEFORE), _write_tree(_R9_AFTER)
    try:
        print("run9-class probe, pre-bug tree :", run_probe(_R9_PROBE, before)[0], "(expect clean)")
        print("run9-class probe, post-bug tree:", run_probe(_R9_PROBE, after)[0], "(expect violated)")
        print("\nFull coverage:  python -m pytest research/_test_verify_repair.py -q")
    finally:
        for d in (before, after):
            shutil.rmtree(d, ignore_errors=True)
