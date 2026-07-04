"""KG-1 — does the grounding rewrite add value when the input is ALREADY pointed?

Charter: docs/CAMPAIGN_CI_HEADLESS.md §6. The pivot's core value claim, measured on the CI fixture
corpus (docs/CI_FIXTURES_SPEC.md). Every prior rewrite win came from VAGUE multi-turn chains; CI
tasks arrive with a traceback (already scoped). This gate tests the rewrite exactly there.

Design (single-shot, stateless — the honest CI model; NO pinning, NO resume):
  For each ADMITTED task × 2 arms, interleaved per task:
    - reset httpx -> apply the seed -> the task's captured pytest evidence is the CI trigger.
    - Arm A (raw-cold):   prompt = instruction + the raw evidence.
    - Arm B (rewrite):    prompt = prepare_no_session(raw)['optimized']  (slm-openai-v2 grounding
                          pass over the SAME raw — target_files, scoped requirements).
    - cold `codex exec` (gpt-5.5) on httpx -> score with the task's recorded pytest_argv -> reset.
  Tokens: codex from --json (uncached = primary); SLM via the harness convention
  (slm_cost_estimate's len//4 + 5k-context floor) counted ALL-IN for arm B.

Gate (pre-registered, charter §6): ε₁ = 1 − (B tokens-per-green / A tokens-per-green), all-in.
  CONTINUE ε₁ ≥ 0.15 AND green(B) ≥ green(A); KILL ε₁ ≤ 0 OR green(B) < green(A); else REPLICATE.
  (Reported both ALL-IN and codex-uncached-only — the latter isolates the exploration effect.)

Run:  PYTHONPATH=<worktree> python research/kg1_rewrite_value.py [--dry-run] [--task ID]
Dry-run = build both prompts (runs the SLM for arm B) but NO codex call (~$0, validates plumbing).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = Path(__file__).resolve().parents[1]


def _load_env():
    for cand in (_ROOT / ".env", Path("B:/LLM/.env")):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


INSTRUCTION = (
    "The tests below are failing in CI. Fix the underlying bug in the library SOURCE so the tests "
    "pass. Do NOT modify the tests. Do not run any networked commands.")

TRANSCRIPT_DIR = Path(__file__).parent / "data" / "kg1_rewrite_value"   # ignored (large .jsonl)
OUT = Path(__file__).parent / "kg1_data"                                # TRACKED (curated summaries)


def build_raw(evidence: str) -> str:
    return "{0}\n\n[Failing test output]\n{1}".format(INSTRUCTION, evidence.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build prompts (SLM only), no codex")
    ap.add_argument("--task", help="single task id")
    args = ap.parse_args()

    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ABORT: OPENAI_API_KEY not set (needed for the slm-openai-v2 rewrite arm).")
    os.environ.setdefault("CODEX_MODEL", "gpt-5.5")
    os.environ.setdefault("CODEX_TIMEOUT_SEC", "1200")   # the T2 lesson: never the 300s default

    # service_tier preflight (desktop-app clobber gotcha)
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.exists():
        for ln in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip().startswith("service_tier"):
                sys.exit("ABORT: active service_tier in ~/.codex/config.toml — comment it out.")

    import ci_fixtures as cf
    import chain_test_v2 as ct
    ct._NORMALIZER_NAME = "slm-openai-v2"
    from chain_test_v2 import prepare_no_session
    from agentic_variety_test import _run_one, _parse_one, slm_cost_estimate

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]              # review P2: iterate ADMITTED only
    tasks_by_id = {t["id"]: t for t in cf.TASKS}
    if args.task:
        admitted = [a for a in admitted if a["id"] == args.task]
    OUT.mkdir(parents=True, exist_ok=True)

    def slm_tokens(raw, grounded):
        return len(raw) // 4 + 5000, len(grounded) // 4   # harness convention (slm_cost_estimate)

    def run_arm(a, task, arm):
        cf.reset_repo()
        err = cf.apply_edits(task)
        if err:
            return dict(task=a["id"], arm=arm, error="seed: " + err)
        evidence = (cf.OUT / a["id"] / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        raw = build_raw(evidence)
        slm_in = slm_out = 0
        if arm == "raw":
            prompt = raw
        else:
            prepared = prepare_no_session(raw, cf.HTTPX, "codex")
            prompt = prepared["optimized"]
            slm_in, slm_out = slm_tokens(raw, prepared.get("grounded", ""))
        rec = dict(task=a["id"], arm=arm, prompt_chars=len(prompt),
                   slm_in=slm_in, slm_out=slm_out)
        if args.dry_run:
            rec["dry_run"] = True
            cf.reset_repo()
            return rec
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPT_DIR / "{0}_{1}.jsonl".format(a["id"], arm)
        t0 = time.time()
        wall, rc = _run_one(prompt, out_path, cf.HTTPX, "codex", session_id=None)  # cold
        u = _parse_one(out_path, "codex")
        rc_test, test_out = cf.run_targets(task)          # recorded pytest_argv
        green = (rc_test == 0)
        rec.update(rc=rc, wall_s=round(wall, 1), timed_out=(rc == 124), green=green,
                   codex_uncached=u.get("uncached_tokens", u.get("input_tokens", 0) - u.get("cached_tokens", 0)),
                   codex_gross=u.get("input_tokens", 0), codex_output=u.get("output_tokens", 0),
                   codex_cached=u.get("cached_tokens", 0), tool_calls=u.get("tool_calls", 0))
        cf.reset_repo()
        return rec

    results = []
    for a in admitted:
        task = tasks_by_id[a["id"]]
        for arm in ("raw", "rewrite"):        # interleaved per task
            print("[kg1] {0} / {1} ...".format(a["id"], arm), flush=True)
            r = run_arm(a, task, arm)
            if not args.dry_run:
                print("      green={0} uncached={1:,} slm={2:,} wall={3}s rc={4}".format(
                    r.get("green"), r.get("codex_uncached", 0), r.get("slm_in", 0) + r.get("slm_out", 0),
                    r.get("wall_s"), r.get("rc")), flush=True)
            else:
                print("      prompt={0:,}c slm_est={1:,} tok".format(
                    r["prompt_chars"], r["slm_in"] + r["slm_out"]), flush=True)
            results.append(r)

    tag = "dryrun" if args.dry_run else "run"
    (OUT / "kg1_{0}.json".format(tag)).write_text(json.dumps(
        dict(generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             model=os.environ["CODEX_MODEL"], n_tasks=len(admitted), results=results),
        indent=2), encoding="utf-8")

    if args.dry_run:
        print("\nDRY RUN complete — {0} prompts built. Inspect OUT/kg1_dryrun.json.".format(len(results)))
        return

    # ---- aggregate + pre-registered gate ----
    def arm_rows(arm):
        return [r for r in results if r.get("arm") == arm and "green" in r]
    def tpg(rows, allin=True):
        green = sum(1 for r in rows if r["green"])
        tok = sum(r["codex_uncached"] + (r["slm_in"] + r["slm_out"] if allin else 0) for r in rows)
        return green, tok, (tok / green if green else float("inf"))
    A, B = arm_rows("raw"), arm_rows("rewrite")
    gA, tA, pA = tpg(A); gB, tB, pB = tpg(B)
    gAu, tAu, pAu = tpg(A, allin=False); _, _, pBu = tpg(B, allin=False)
    eps = 1 - pB / pA if pA not in (0, float("inf")) else float("nan")
    eps_u = 1 - pBu / pAu if pAu not in (0, float("inf")) else float("nan")
    print("\n== KG-1 result ==")
    print("  RAW    : green {0}/{1}  all-in tok/green={2:,.0f}  (codex-only {3:,.0f})".format(gA, len(A), pA, pAu))
    print("  REWRITE: green {0}/{1}  all-in tok/green={2:,.0f}  (codex-only {3:,.0f})".format(gB, len(B), pB, pBu))
    print("  ε₁ (all-in) = {0:+.3f}   ε₁ (codex-uncached only) = {1:+.3f}".format(eps, eps_u))
    if gB < gA:
        verdict = "KILL (rewrite regressed correctness: green {0} < {1})".format(gB, gA)
    elif eps >= 0.15 and gB >= gA:
        verdict = "CONTINUE (ε₁ ≥ 0.15 at green-parity)"
    elif eps <= 0:
        verdict = "KILL (ε₁ ≤ 0)"
    else:
        verdict = "REPLICATE (0 < ε₁ < 0.15)"
    print("  VERDICT:", verdict)
    summ = dict(green_raw=gA, green_rewrite=gB, tpg_raw_allin=pA, tpg_rewrite_allin=pB,
                eps1_allin=eps, eps1_codex_only=eps_u, verdict=verdict)
    (OUT / "kg1_verdict.json").write_text(json.dumps(summ, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
