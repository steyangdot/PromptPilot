"""KG-1 — does the grounding rewrite add value when the input is ALREADY pointed?

Charter: docs/CAMPAIGN_CI_HEADLESS.md §6. The pivot's core value claim, measured on the CI fixture
corpus (docs/CI_FIXTURES_SPEC.md). Every prior rewrite win came from VAGUE multi-turn chains; CI
tasks arrive with a traceback (already scoped). This gate tests the rewrite exactly there.

Design (single-shot, stateless — the honest CI model; NO pinning, NO resume):
  For each ADMITTED task × 2 arms, interleaved per task:
    - reset httpx to the manifest base -> apply the seed -> COMMIT it (throwaway commit).
    - Arm A (raw-cold):   prompt = instruction + the raw evidence.
    - Arm B (rewrite):    prompt = prepare_no_session(raw)['optimized']  (slm-openai-v2 grounding
                          pass over the SAME raw — target_files, scoped requirements).
    - cold `codex exec` (gpt-5.5) on httpx -> score with the task's recorded pytest_argv ->
      reset --hard back to base.

PROTOCOL v2 (2026-07-05, post gate-script review — the v1 run predates these):
  * COMMITTED SEED (review A-1): v1 applied seeds as uncommitted working-tree edits, so arm B's
    SLM repo-context collector saw the seed in `git status`/`git diff` — an oracle pointer to the
    bug file that does not exist in real CI (where the failing state is committed). Seeds are now
    committed to a throwaway commit; the tree codex+SLM see is CLEAN.
  * MEASURED SLM TOKENS (review A-2): v1 charged the len//4+5000 estimate; its error band
    straddles the pre-registered 0.15 CONTINUE line. All in-process OpenAI chat calls (classify +
    rewrite passes) are now tapped and their real usage summed; the estimate remains only as a
    marked fallback.
  * CENSORING (review A-3): timed-out or usage-less codex runs are recorded but EXCLUDED from the
    ratio (a killed run parses as ~0 tokens — the audit-history inflation bug) and reported.
  * INTEGRITY GUARD (reviews A-5/D-F4): with the seed committed, `git status --porcelain` after
    the codex run is exactly the agent's edits regardless of write channel. Any touch of tests/,
    conftest.py, pytest.ini, pyproject.toml or setup.cfg is recorded and forfeits the green
    (green = pytest-green AND no test-touch; green_pytest preserves the raw outcome).
  * APPEND-ONLY ARTIFACTS (review A-8a): run/verdict files are label-suffixed (--corpus/--task);
    the published v1 kg1_run.json / kg1_verdict.json are immutable records, never rewritten.
    Results persist incrementally after every run. Prompts are persisted for audit (A-8b) and
    PROMPTPILOT_USE_TARGET_HINT is recorded in the header (A-8c).

Gate (charter §6, METRIC AMENDED 2026-07-05 — pre-registered before any v2 outcome existed;
the amending commit is the proof):
  PRIMARY ε₁ = 1 − (B codex-uncached tokens-per-green / A codex-uncached tokens-per-green).
  LLM (codex) tokens only: the SLM is nano-priced (~0.02× gpt-5.5), so charging it 1:1 in the
  gated metric over-weights it ~50×; doctrine keeps SLM as its own reported line, never folded
  into the LLM headline. SLM cost is still MEASURED and reported (all-in 1:1 + cost-weighted
  0.02×) so nothing is hidden — a rewrite arm that burned absurd SLM would show up there.
  Bands unchanged: CONTINUE ε₁ ≥ 0.15 AND green(B) ≥ green(A); KILL ε₁ ≤ 0 OR
  green(B) < green(A); else REPLICATE.
  (The v1 run was gated on the ORIGINAL all-in definition and its record stands as-decided,
  with the measured-SLM and seed-leak caveats recorded in the charter OUTCOMES.)

Run:  PYTHONPATH=<worktree> python research/kg1_rewrite_value.py [--dry-run] [--task ID]
                                                                 [--corpus N] [--force]
Dry-run = build both prompts (runs the SLM for arm B) but NO codex call (~$0, validates plumbing).
"""
from __future__ import annotations

import argparse
import hashlib
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


# --- measured SLM usage (review A-2) ---------------------------------------------------------
_SLM_CALLS: list = []   # (prompt_tokens, completion_tokens) per in-process OpenAI chat call


def _install_slm_usage_tap() -> bool:
    """Tap every OpenAI chat call in-process (the v2 normalizer's classify AND rewrite passes)
    so arm B is charged its REAL usage, not the len//4+5000 estimate whose error band straddles
    the pre-registered 0.15 line. Wraps the client method; no prpt code is modified."""
    try:
        from openai.resources.chat import completions as _c
    except Exception:
        return False
    orig = _c.Completions.create

    def create(self, *a, **kw):
        resp = orig(self, *a, **kw)
        try:
            u = getattr(resp, "usage", None)
            if u is not None:
                _SLM_CALLS.append((int(u.prompt_tokens), int(u.completion_tokens)))
        except Exception:
            pass
        return resp

    _c.Completions.create = create
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build prompts (SLM only), no codex")
    ap.add_argument("--task", help="single task id")
    ap.add_argument("--corpus", type=int, help="run only tasks with this corpus tag (2 = v2 set)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing run artifact")
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
    from agentic_variety_test import _run_one, _parse_one

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]              # review P2: iterate ADMITTED only
    base = manifest["base_commit"]
    tasks_by_id = {t["id"]: t for t in cf.TASKS}
    if args.corpus:
        admitted = [a for a in admitted if a.get("corpus", 1) == args.corpus]
    if args.task:
        admitted = [a for a in admitted if a["id"] == args.task]
    if not admitted:
        sys.exit("no admitted tasks match the filters")
    OUT.mkdir(parents=True, exist_ok=True)

    # Append-only artifacts (review A-8a).
    label = ("task-" + args.task if args.task
             else "c{0}".format(args.corpus) if args.corpus else "all")
    run_path = OUT / ("kg1_dryrun_{0}.json".format(label) if args.dry_run
                      else "kg1_run_{0}.json".format(label))
    verdict_path = OUT / "kg1_verdict_{0}.json".format(label)
    if not args.dry_run and run_path.exists() and not args.force:
        sys.exit("{0} exists — run artifacts are append-only records; --force to overwrite "
                 "deliberately.".format(run_path))

    # --- git helpers on the fixture repo (committed-seed protocol, review A-1) ---
    def g(*gargs, timeout=60):
        rc, out = cf.sh(["git", *gargs], timeout=timeout)
        return rc, out.strip()

    def reset_to_base():
        # cf.reset_repo (checkout -- . + clean) cannot undo the throwaway seed COMMIT.
        g("reset", "--hard", base)
        g("clean", "-fdx", "--quiet")

    def ensure_base_strict():
        rc, head = g("rev-parse", "--short", "HEAD")
        if rc != 0:
            sys.exit("ABORT: git rev-parse failed in the fixture repo")
        if head == base:
            return
        _, msg = g("log", "-1", "--format=%s")
        _, parent = g("rev-parse", "--short", "HEAD~1")
        if msg.startswith("seed: ") and parent == base:
            reset_to_base()          # leftover throwaway from a crashed run
            return
        sys.exit("ABORT: fixture repo at {0} (manifest base {1}) and HEAD is not a throwaway "
                 "seed commit — resolve manually before running.".format(head, base))

    ensure_base_strict()
    slm_tap = _install_slm_usage_tap()
    if not slm_tap:
        print("[kg1] WARNING: SLM usage tap unavailable — falling back to the len//4 estimate",
              flush=True)

    def slm_tokens_est(raw, grounded):
        return len(raw) // 4 + 5000, len(grounded) // 4   # legacy convention (fallback only)

    def run_arm(a, task, arm):
        reset_to_base()
        err = cf.apply_edits(task)
        if err:
            return dict(task=a["id"], arm=arm, error="seed: " + err)
        # COMMIT the seed (review A-1): real CI triggers on a COMMITTED failing state; an
        # uncommitted seed leaks the bug diff + file name into arm B's SLM repo context.
        if task["edits"]:
            rc_c, out_c = g("-c", "user.name=kg1-harness", "-c", "user.email=kg1@local",
                            "commit", "-am", "seed: {0} (throwaway)".format(a["id"]))
            if rc_c != 0:
                reset_to_base()
                return dict(task=a["id"], arm=arm, error="seed-commit: " + out_c[:200])
        _, porc0 = g("status", "--porcelain")
        if porc0:
            reset_to_base()
            return dict(task=a["id"], arm=arm, error="tree dirty after seed commit: " + porc0[:200])
        evidence = (cf.OUT / a["id"] / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        raw = build_raw(evidence)
        slm_in = slm_out = slm_calls = 0
        slm_measured = False
        if arm == "raw":
            prompt = raw
        else:
            _SLM_CALLS.clear()
            prepared = prepare_no_session(raw, cf.HTTPX, "codex")
            prompt = prepared["optimized"]
            if _SLM_CALLS:
                slm_in = sum(i for i, _ in _SLM_CALLS)
                slm_out = sum(o for _, o in _SLM_CALLS)
                slm_measured, slm_calls = True, len(_SLM_CALLS)
            else:
                slm_in, slm_out = slm_tokens_est(raw, prepared.get("grounded", ""))
        rec = dict(task=a["id"], arm=arm, prompt_chars=len(prompt),
                   prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
                   rewrite_changed=(arm == "rewrite" and prompt.strip() != raw.strip()),
                   slm_in=slm_in, slm_out=slm_out,
                   slm_measured=slm_measured, slm_calls=slm_calls)
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        (TRANSCRIPT_DIR / "{0}_{1}_prompt.txt".format(a["id"], arm)).write_text(
            prompt, encoding="utf-8")                      # audit trail (review A-8b)
        if args.dry_run:
            rec["dry_run"] = True
            reset_to_base()
            return rec
        out_path = TRANSCRIPT_DIR / "{0}_{1}.jsonl".format(a["id"], arm)
        wall, rc = _run_one(prompt, out_path, cf.HTTPX, "codex", session_id=None)  # cold
        u = _parse_one(out_path, "codex")
        # Integrity (reviews A-5/D-F4): with the seed committed, porcelain == exactly the
        # agent's edits, regardless of write channel (shell writes evade the patch envelope).
        _, porc = g("status", "--porcelain")
        touched = [ln[3:].strip().replace("\\", "/") for ln in porc.splitlines() if ln.strip()]
        touched_tests = [p for p in touched
                         if p.startswith("tests/") or Path(p).name in ("conftest.py", "pytest.ini")
                         or p in ("pyproject.toml", "setup.cfg")]
        _, diff_stat = g("diff", "HEAD", "--stat")
        rc_test, _test_out = cf.run_targets(task)          # recorded pytest_argv
        green_pytest = (rc_test == 0)
        # Censoring doctrine (review A-3): a timed-out or usage-less run must not enter the
        # ratio as a free green / zero-token row (killed runs parse as ~0 tokens).
        censored = (rc == 124) or (u.get("input_tokens", 0) == 0 and u.get("output_tokens", 0) == 0)
        rec.update(rc=rc, wall_s=round(wall, 1), timed_out=(rc == 124), censored=censored,
                   green_pytest=green_pytest,
                   green=(green_pytest and not touched_tests),   # teach-to-test guard
                   touched_files=touched[:50], touched_tests=touched_tests,
                   diff_stat=diff_stat[-1200:],
                   codex_uncached=u.get("uncached_tokens", u.get("input_tokens", 0) - u.get("cached_tokens", 0)),
                   codex_gross=u.get("input_tokens", 0), codex_output=u.get("output_tokens", 0),
                   codex_cached=u.get("cached_tokens", 0), tool_calls=u.get("tool_calls", 0))
        reset_to_base()
        _, head_now = g("rev-parse", "--short", "HEAD")
        if head_now != base:
            sys.exit("ABORT: fixture repo failed to reset to base after {0}/{1}".format(a["id"], arm))
        return rec

    header = dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        model=os.environ["CODEX_MODEL"], n_tasks=len(admitted), corpus=args.corpus,
        base_commit=base,
        protocol="v2: committed-seed / measured-slm / censoring / integrity-guard",
        slm_tap=slm_tap,
        target_hint=os.environ.get("PROMPTPILOT_USE_TARGET_HINT", "<unset>"),
        codex_timeout_sec=os.environ["CODEX_TIMEOUT_SEC"])
    results = []

    def persist():
        run_path.write_text(json.dumps(dict(header, results=results), indent=2),
                            encoding="utf-8")

    for a in admitted:
        task = tasks_by_id[a["id"]]
        for arm in ("raw", "rewrite"):        # interleaved per task
            print("[kg1] {0} / {1} ...".format(a["id"], arm), flush=True)
            r = run_arm(a, task, arm)
            if not args.dry_run:
                print("      green={0} uncached={1:,} slm={2:,}{3} wall={4}s rc={5}{6}".format(
                    r.get("green"), r.get("codex_uncached", 0),
                    r.get("slm_in", 0) + r.get("slm_out", 0),
                    "(meas)" if r.get("slm_measured") else "(est)",
                    r.get("wall_s"), r.get("rc"),
                    " CENSORED" if r.get("censored") else
                    (" TOUCHED-TESTS!" if r.get("touched_tests") else "")), flush=True)
            else:
                print("      prompt={0:,}c slm={1:,} tok{2}".format(
                    r["prompt_chars"], r["slm_in"] + r["slm_out"],
                    " (measured)" if r.get("slm_measured") else " (est)"), flush=True)
            results.append(r)
            persist()                          # incremental (review A-6): a crash loses nothing

    if args.dry_run:
        print("\nDRY RUN complete — {0} prompts built. Inspect {1}.".format(len(results), run_path))
        return

    # ---- aggregate + pre-registered gate ----
    def arm_rows(arm):
        return [r for r in results
                if r.get("arm") == arm and "green" in r and not r.get("censored")]
    n_censored = sum(1 for r in results if r.get("censored"))
    n_error = sum(1 for r in results if "error" in r)
    n_integrity = sum(1 for r in results if r.get("touched_tests"))

    def tpg(rows, allin=True):
        green = sum(1 for r in rows if r["green"])
        tok = sum(r["codex_uncached"] + (r["slm_in"] + r["slm_out"] if allin else 0) for r in rows)
        return green, tok, (tok / green if green else float("inf"))

    A, B = arm_rows("raw"), arm_rows("rewrite")
    if len(A) != len(admitted) or len(B) != len(admitted):
        # Arm-size assertion (review A-6): error/censored rows must not silently shrink a
        # denominator — an incomplete experiment gets an INVALID verdict, not a number.
        verdict = ("INVALID (incomplete arms: raw {0}/{2}, rewrite {1}/{2}; censored={3}, "
                   "errors={4})".format(len(A), len(B), len(admitted), n_censored, n_error))
        print("\n== KG-1 result ==\n  VERDICT:", verdict)
        verdict_path.write_text(json.dumps(dict(
            verdict=verdict, n_censored=n_censored, n_error=n_error), indent=2),
            encoding="utf-8")
        return

    gA, tA, pA = tpg(A)
    gB, tB, pB = tpg(B)
    gAu, tAu, pAu = tpg(A, allin=False)
    _, _, pBu = tpg(B, allin=False)
    eps = 1 - pB / pA if pA not in (0, float("inf")) else float("nan")
    eps_u = 1 - pBu / pAu if pAu not in (0, float("inf")) else float("nan")
    # Cost-weighted variant persisted here (review D-F5: +0.371 previously existed only as
    # session arithmetic). SLM weighted at 0.02× (nano-class pricing vs gpt-5.5).
    tB_cw = sum(r["codex_uncached"] + 0.02 * (r["slm_in"] + r["slm_out"]) for r in B)
    pB_cw = tB_cw / gB if gB else float("inf")
    eps_cw = 1 - pB_cw / pAu if pAu not in (0, float("inf")) else float("nan")

    print("\n== KG-1 result ==")
    print("  RAW    : green {0}/{1}  codex-uncached tok/green={2:,.0f}  (all-in {3:,.0f})".format(
        gA, len(A), pAu, pA))
    print("  REWRITE: green {0}/{1}  codex-uncached tok/green={2:,.0f}  (all-in {3:,.0f})".format(
        gB, len(B), pBu, pB))
    print("  ε₁ PRIMARY (codex-only) = {0:+.3f}   [all-in 1:1 = {1:+.3f}   "
          "cost-weighted 0.02× = {2:+.3f}]".format(eps_u, eps, eps_cw))
    if n_censored or n_integrity:
        print("  NOTE: censored={0} integrity-violations={1}".format(n_censored, n_integrity))
    # Gate on the PRIMARY (codex-only) metric — 2026-07-05 amendment, see module docstring.
    if gB < gA:
        verdict = "KILL (rewrite regressed correctness: green {0} < {1})".format(gB, gA)
    elif eps_u >= 0.15 and gB >= gA:
        verdict = "CONTINUE (codex-only ε₁ ≥ 0.15 at green-parity)"
    elif eps_u <= 0:
        verdict = "KILL (codex-only ε₁ ≤ 0)"
    else:
        verdict = "REPLICATE (0 < codex-only ε₁ < 0.15)"
    print("  VERDICT:", verdict)
    summ = dict(green_raw=gA, green_rewrite=gB,
                tpg_raw_codex=pAu, tpg_rewrite_codex=pBu,
                tpg_raw_allin=pA, tpg_rewrite_allin=pB,
                eps1_primary_codex_only=eps_u, eps1_allin=eps, eps1_cost_weighted=eps_cw,
                gated_metric="codex-uncached only (amended 2026-07-05, pre-v2-outcome)",
                n_censored=n_censored, n_error=n_error, integrity_violations=n_integrity,
                slm_measured_rows=sum(1 for r in B if r.get("slm_measured")),
                verdict=verdict)
    verdict_path.write_text(json.dumps(summ, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
