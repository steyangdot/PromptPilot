#!/usr/bin/env python
r"""Stage-1 honest re-score (docs/SESSION_MEMORY_ROADMAP.md Stage 1).

FREE — no paid run, no model. Re-scores the EXISTING chain_long A/B data with the
Stage-0 measurement-validity fixes (classify_run + the pytest-validity flag) to produce
the honest, mode-separated taxed count, and prints the decision-gate readout.

What it computes (all deterministic, over saved artifacts):
  1. classify_run() per run -> clean / ledger_degraded / no_edit_bail. A degraded run
     (memory not maintained) or a bail (the migration never ran) is NOT a valid test of
     the destructive-migration guard and is EXCLUDED from the tax tally.
  2. The captured-endstate pytest validity/discrimination — to SHOW whether the non-
     circular oracle is even usable on this chain (the risk-register precondition).
  3. A weak diff-contract-survival diagnostic (token presence in the final diff). This is
     NOT a tax oracle — token presence != semantic preservation — and is printed only to
     show that no GROSS contract-drop separates the clean runs.

Honest conclusion the data supports is printed at the end. Read-only.

Usage:  python research/stage1_rescore.py [OUT_DIR]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain_test_v2 as ct  # noqa: E402

DEFAULT = Path("research/data/chain_results_v2/codex/chain_long")

# chain_long contracts C1..C11 the late ResilienceConfig migration (C12-13) must preserve.
_CONTRACT_TOK = {
    "connect_timeout": r"connect_timeout",
    "read_timeout":    r"read_timeout",
    "retry_after":     r"retry[_-]?after",
    "retry_cap":       r"max_retry|retry.*max|max.*delay",
    "elapsed":         r"elapsed",
    "stats":           r"_stats|Stats",
    "pool_size":       r"pool_size",
    "resilience":      r"ResilienceConfig|resilience",
}


def _added(diff: str) -> str:
    return "\n".join(l for l in (diff or "").splitlines()
                     if l.startswith("+") and not l.startswith("+++"))


def _pytest_valid(es: dict) -> bool | None:
    """Same rc->valid rule as chain_test_v2._pytest_flags; derive for pre-flag artifacts."""
    v = es.get("pytest_valid")
    if v is not None:
        return v
    rc = es.get("pytest_rc")
    return (rc in (0, 1)) if rc is not None else None


def rescore(out_dir: Path) -> dict:
    arms = [a for a in ("with_session", "with_memory")
            if (out_dir / f"{a}_run1.json").exists()]
    result: dict = {}
    for arm in arms:
        runs = sorted(int(re.search(r"_run(\d+)\.json$", str(p)).group(1))
                      for p in out_dir.glob(f"{arm}_run*.json"))
        rows = []
        for r in runs:
            recs = json.loads((out_dir / f"{arm}_run{r}.json").read_text(encoding="utf-8"))
            c = ct.classify_run(recs)
            esp = out_dir / f"endstate_{arm}_run{r}.json"
            es = json.loads(esp.read_text(encoding="utf-8")) if esp.exists() else {}
            add = _added(es.get("diff", ""))
            survived = {k: bool(re.search(p, add, re.I)) for k, p in _CONTRACT_TOK.items()}
            rows.append({
                "run": r, "class": c["class"],
                "ledger_failed_turns": c["ledger_failed_turns"],
                "pytest_rc": es.get("pytest_rc"), "pytest_valid": _pytest_valid(es),
                "missing_contracts": [k for k, ok in survived.items() if not ok],
            })
        result[arm] = rows
    return result


def _print(out_dir: Path) -> None:
    res = rescore(out_dir)
    print(f"Stage-1 honest re-score  (out={out_dir})")
    print("=" * 92)
    print("(diff_missing_contracts is a COARSE token diagnostic, NOT the tax signal — token presence "
          "!= semantic preservation; the real per-run tax is the forensic apply-to-baseline oracle.)")
    for arm, rows in res.items():
        print(f"\n### {arm}")
        for x in rows:
            miss = ",".join(x["missing_contracts"]) or "-"
            print(f"  run{x['run']}: class={x['class']:15s} "
                  f"pytest_rc={x['pytest_rc']} valid={x['pytest_valid']} "
                  f"diff_missing_contracts=[{miss}]")
        clean = [x["run"] for x in rows if x["class"] == "clean"]
        excl = [(x["run"], x["class"]) for x in rows if x["class"] != "clean"]
        print(f"  -> clean (eligible): {clean}    EXCLUDED: {excl or '(none)'}")

    print("\n" + "=" * 92)
    print("VERDICT")
    wm = res.get("with_memory", [])
    ws = res.get("with_session", [])
    wm_excl = [(x["run"], x["class"]) for x in wm if x["class"] != "clean"]
    wm_clean = [x["run"] for x in wm if x["class"] == "clean"]
    ws_clean = [x["run"] for x in ws if x["class"] == "clean"]
    # Is the STORED in-place endstate pytest discriminative? (same rc on clean+excluded => no)
    rcs = {x["pytest_rc"] for x in wm + ws}
    discriminative = len(rcs) > 1
    print(f"- classify_run EXCLUDES {len(wm_excl)}/{len(wm)} with_memory runs as non-valid tests of the "
          f"destructive-migration guard: {wm_excl}.")
    print(f"  Eligible (clean) denominator: with_memory {len(wm_clean)} {wm_clean} vs "
          f"with_session {len(ws_clean)} {ws_clean}.")
    print(f"- The STORED in-place endstate pytest is "
          f"{'DISCRIMINATIVE' if discriminative else 'NON-DISCRIMINATIVE'} (rc set={sorted(rcs)}): it "
          f"cannot separate taxed from clean, so the tax CANNOT be read off the stored endstate — "
          f"chain_long's IN-PLACE oracle is the disabled one (risk-register precondition).")
    print("- The per-run tax on the CLEAN runs was instead established by the prior FORENSIC oracle "
          "(apply each run's diff to a clean d764bfc worktree + run the orphaned test, "
          "pass-clean/fail-patched; see memory `memory_ab_result`): wm-run1 (17 orphaned-test "
          "TypeErrors) + wm-run3 (removed limits= -> orphaned test_pool_timeout) CONFIRMED taxed, "
          "wm-run5 clean  =>  2/3 clean taxed; with_session destructive ~1/5.")
    print("\nDECISION (roadmap Stage-1 gate): the '4/5' headline was INFLATED by the 2 degraded/bail runs, "
          "BUT a REAL residual destructive-migration tax REMAINS on the clean runs (2/3) — so with_memory "
          "is NOT rescued to parity, and it was also LESS RELIABLE (2/5 degraded/bail vs with_session "
          "0/5). The continuity thesis stays UNSUPPORTED. This is the 'real residual tax' branch, NOT "
          "'measurement artifact'. Because chain_long's in-place oracle can't validate a fix, validate "
          "any Stage-2 fix (e.g. the additive-bias guard rewording) on the test-writing oracle fixtures "
          "(research/_oracle_groundtruth.py), where pytest IS discriminative in-place.")


if __name__ == "__main__":
    _print(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT)
