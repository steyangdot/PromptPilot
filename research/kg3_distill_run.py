"""KG-3 — does an ADDITIVE, verified triage note (below intact evidence) beat raw+pin?

Charter docs/CAMPAIGN_CI_HEADLESS.md §6 (parked hypothesis) + docs/PHASE3_PRPT_CI_DESIGN.md.
The KG-1 v2 rewrite KILLed because REPLACING evidence with prose destroyed the verification
anchor (24-vs-2 full-suite escalations; docs/FINDING_REWRITE_TOKEN_ECONOMICS.md). KG-3 tests
the loss-flooring redesign: keep the verbatim traceback, keep the same verification pin in
BOTH arms, and in arm B APPEND a tightened, grep-verified diagnosis note (research/kg3_note.py).
Hypothesis: preserves the win (correct diagnosis saves exploration) while flooring the loss
(evidence intact → verification stays file-scoped) → pooled codex-only ε₁ ≥ +0.10.

  Arm A (baseline):  instruction + VERBATIM evidence + verification pin.
  Arm B (note):      arm A + the appended [Likely cause ...] note.
Both arms: committed-seed clean tree (protocol v2, no diff leak), cold `codex exec` (gpt-5.5),
scored by the task's recorded pytest_argv, N reps/task interleaved A-then-B per rep.

Gate is PRE-REGISTERED (see the charter KG-3 block, committed before any run): primary =
pooled codex-uncached tok/green over ALL rep-runs, ε₁ = 1 − (B/A). CONTINUE ε₁ ≥ +0.10 AND
green(B) ≥ green(A); KILL ε₁ ≤ 0 OR green(B) < green(A); else REPLICATE. Secondary (reported,
not gated): per-task paired B-vs-A win-rate across reps; all-in and cost-weighted ε₁; the
gappy-evidence subgroup (frozen module-mismatch). SLM measured, never folded into the gate.

Run:  PYTHONPATH=<worktree> python research/kg3_distill_run.py [--reps N] [--dry-run]
                                                               [--task ID] [--force]
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

INSTRUCTION = (
    "The tests below are failing in CI. Fix the underlying bug in the library SOURCE so the tests "
    "pass. Do NOT modify the tests. Do not run any networked commands.")
TRANSCRIPT_DIR = Path(__file__).parent / "data" / "kg3_distill"     # ignored
OUT = Path(__file__).parent / "kg1_data"                            # tracked summaries

_SLM_CALLS = []


def _load_env():
    for cand in (_ROOT / ".env", Path("B:/LLM/.env")):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _install_slm_usage_tap() -> bool:
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


def verification_pin(task, cf) -> str:
    argv = " ".join(["python -m pytest", *task["targets"], "-q"]
                    + (["-k", repr(task["k"])] if task.get("k") else []))
    return ("\n\n[Verification] Verify your fix with at least: `{0}`. Do NOT run the full test "
            "suite — it exceeds the command timeout and the CI gate runs it separately.".format(argv))


def build_A(evidence: str, task, cf) -> str:
    return "{0}\n\n[Failing test output]\n{1}{2}".format(
        INSTRUCTION, evidence.strip(), verification_pin(task, cf))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="reps per task per arm (repeated measures)")
    ap.add_argument("--dry-run", action="store_true", help="build prompts + notes (SLM), no codex")
    ap.add_argument("--task", help="single task id")
    ap.add_argument("--force", action="store_true", help="overwrite an existing run artifact")
    args = ap.parse_args()

    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ABORT: OPENAI_API_KEY not set (needed for the arm-B note).")
    os.environ.setdefault("CODEX_MODEL", "gpt-5.5")
    os.environ.setdefault("CODEX_TIMEOUT_SEC", "1200")
    if not args.dry_run:                       # codex config is irrelevant to a $0 dry-run
        cfg = Path.home() / ".codex" / "config.toml"
        if cfg.exists():
            for ln in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip().startswith("service_tier"):
                    sys.exit("ABORT: active service_tier in ~/.codex/config.toml — comment it out.")

    import ci_fixtures as cf
    import chain_test_v2 as ct
    ct._NORMALIZER_NAME = "slm-openai-v2"
    from agentic_variety_test import _run_one, _parse_one
    from kg3_note import build_note

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]
    base = manifest["base_commit"]
    tasks_by_id = {t["id"]: t for t in cf.TASKS}
    if args.task:
        admitted = [a for a in admitted if a["id"] == args.task]
    if not admitted:
        sys.exit("no admitted tasks match the filters")
    OUT.mkdir(parents=True, exist_ok=True)

    label = "task-" + args.task if args.task else "all"
    run_path = OUT / ("kg3_dryrun_{0}.json".format(label) if args.dry_run
                      else "kg3_run_{0}.json".format(label))
    verdict_path = OUT / "kg3_verdict_{0}.json".format(label)
    if not args.dry_run and run_path.exists() and not args.force:
        sys.exit("{0} exists — append-only record; --force to overwrite.".format(run_path))

    def g(*a, timeout=60):
        rc, out = cf.sh(["git", *a], timeout=timeout)
        return rc, out.strip()

    def reset_to_base():
        g("reset", "--hard", base); g("clean", "-fdx", "--quiet")

    def ensure_base_strict():
        rc, head = g("rev-parse", "--short", "HEAD")
        if rc != 0:
            sys.exit("ABORT: git rev-parse failed in the fixture repo")
        if head == base:
            return
        _, msg = g("log", "-1", "--format=%s"); _, parent = g("rev-parse", "--short", "HEAD~1")
        if msg.startswith("seed: ") and parent == base:
            reset_to_base(); return
        sys.exit("ABORT: fixture repo at {0} (base {1}) and HEAD not a throwaway seed.".format(head, base))

    ensure_base_strict()
    if not _install_slm_usage_tap():
        print("[kg3] WARNING: SLM usage tap unavailable", flush=True)

    def seed_and_commit(task, tid):
        """Return (seed_sha, err). seed_sha is the commit BOTH arms must reset to per rep
        (== base for the pre-seeded task, which has no throwaway commit). Resets on every
        error path so a partial seed never leaks to the next rep."""
        reset_to_base()
        err = cf.apply_edits(task)
        if err:
            reset_to_base()
            return None, "seed: " + err
        if task["edits"]:
            rc_c, out_c = g("-c", "user.name=kg3", "-c", "user.email=kg3@local",
                            "commit", "-am", "seed: {0} (throwaway)".format(tid))
            if rc_c != 0:
                reset_to_base()
                return None, "seed-commit: " + out_c[:160]
        _, porc = g("status", "--porcelain")
        if porc:
            reset_to_base()
            return None, "tree dirty after seed commit: " + porc[:160]
        _, seed_sha = g("rev-parse", "HEAD")
        return seed_sha, None

    def run_arm(a, task, arm, rep, note, seed_sha):
        # CRITICAL (review #1): reset to the per-rep SEED COMMIT before EACH arm so arm B does
        # not inherit arm A's fix. Not base — that would un-seed non-preseeded tasks.
        g("reset", "--hard", seed_sha); g("clean", "-fdx", "--quiet")
        evidence = (cf.OUT / a["id"] / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        prompt = build_A(evidence, task, cf)
        if arm == "B" and note:
            prompt = prompt + "\n" + note
        rec = dict(task=a["id"], arm=arm, rep=rep, prompt_chars=len(prompt),
                   prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
                   note_present=(arm == "B" and bool(note)))
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        (TRANSCRIPT_DIR / "{0}_{1}_r{2}_prompt.txt".format(a["id"], arm, rep)).write_text(
            prompt, encoding="utf-8")
        if args.dry_run:
            rec["dry_run"] = True
            return rec
        out_path = TRANSCRIPT_DIR / "{0}_{1}_r{2}.jsonl".format(a["id"], arm, rep)
        wall, rc = _run_one(prompt, out_path, cf.HTTPX, "codex", session_id=None)
        u = _parse_one(out_path, "codex")
        _, porc = g("status", "--porcelain")
        touched = [ln[3:].strip().replace("\\", "/") for ln in porc.splitlines() if ln.strip()]
        touched_tests = [p for p in touched
                         if p.startswith("tests/") or Path(p).name in ("conftest.py", "pytest.ini")
                         or p in ("pyproject.toml", "setup.cfg")]
        rc_test, _ = cf.run_targets(task)
        green_pytest = (rc_test == 0)
        censored = (rc == 124) or (u.get("input_tokens", 0) == 0 and u.get("output_tokens", 0) == 0)
        rec.update(rc=rc, wall_s=round(wall, 1), timed_out=(rc == 124), censored=censored,
                   green_pytest=green_pytest, green=(green_pytest and not touched_tests),
                   touched_tests=touched_tests,
                   codex_uncached=u.get("uncached_tokens", u.get("input_tokens", 0) - u.get("cached_tokens", 0)),
                   codex_gross=u.get("input_tokens", 0), codex_output=u.get("output_tokens", 0),
                   codex_cached=u.get("cached_tokens", 0), tool_calls=u.get("tool_calls", 0))
        return rec

    header = dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        model=os.environ["CODEX_MODEL"], base_commit=base, reps=args.reps,
        n_tasks=len(admitted), protocol="KG-3 v1: raw+pin (A) vs raw+pin+note (B), committed-seed",
        slm_tap=True, codex_timeout_sec=os.environ["CODEX_TIMEOUT_SEC"])
    results = []
    notes = {}

    def persist():
        run_path.write_text(json.dumps(dict(header, note_meta=notes, results=results), indent=2),
                            encoding="utf-8")

    # Live smoke (review #3): one real codex round-trip + token parse before the 96-run batch,
    # so a broken auth/model/service_tier aborts for ONE run's cost, not the whole spend. The
    # smoke run is a throwaway (not scored into the experiment).
    if not args.dry_run:
        s0 = admitted[0]; stask = tasks_by_id[s0["id"]]
        print("[kg3] SMOKE: one live codex run on {0} before the batch...".format(s0["id"]), flush=True)
        s_sha, s_err = seed_and_commit(stask, s0["id"])
        if s_err:
            sys.exit("ABORT smoke: seed failed: " + s_err)
        g("reset", "--hard", s_sha); g("clean", "-fdx", "--quiet")
        _sevi = (cf.OUT / s0["id"] / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        sw, src = _run_one(build_A(_sevi, stask, cf), TRANSCRIPT_DIR / "_smoke.jsonl",
                           cf.HTTPX, "codex", session_id=None)
        su = _parse_one(TRANSCRIPT_DIR / "_smoke.jsonl", "codex")
        reset_to_base()
        s_in = su.get("input_tokens", 0)
        if src == 124 or s_in == 0:
            sys.exit("ABORT smoke: codex rc={0}, input_tokens={1} — broken auth/model/service_tier. "
                     "0 tokens is the service_tier=priority gotcha: set CODEX_SERVICE_TIER=fast (or "
                     "comment out service_tier in ~/.codex/config.toml) and retry.".format(src, s_in))
        print("[kg3] SMOKE OK: rc={0}, uncached={1:,}, wall={2:.0f}s — starting batch.".format(
            src, su.get("uncached_tokens", s_in - su.get("cached_tokens", 0)), sw), flush=True)

    for a in admitted:
        tid = a["id"]
        task = tasks_by_id[tid]
        for rep in range(args.reps):
            seed_sha, err = seed_and_commit(task, tid)
            if err:
                results.append(dict(task=tid, rep=rep, error=err)); persist(); continue
            # Build the note ONCE per rep on the seeded tree (arm B input); measure its SLM.
            _SLM_CALLS.clear()
            _evi = (cf.OUT / tid / "evidence.txt").read_text(encoding="utf-8", errors="replace")
            nb = build_note(_evi, cf.HTTPX, lambda p: g("cat-file", "-e", "HEAD:" + p)[0] == 0)
            slm_in = sum(i for i, _ in _SLM_CALLS); slm_out = sum(o for _, o in _SLM_CALLS)
            notes["{0}_r{1}".format(tid, rep)] = dict(
                note=nb["note"], mechanism=nb["mechanism"], file=nb["file"],
                ok=nb["ok"], slm_in=slm_in, slm_out=slm_out)
            for arm in ("A", "B"):
                print("[kg3] {0} rep{1} / {2}{3} ...".format(
                    tid, rep, arm, "(note)" if arm == "B" and nb["note"] else ""), flush=True)
                r = run_arm(a, task, arm, rep, nb["note"], seed_sha)
                if arm == "B":
                    r["slm_in"], r["slm_out"] = slm_in, slm_out
                if not args.dry_run:
                    print("      green={0} uncached={1:,} wall={2}s rc={3}{4}".format(
                        r.get("green"), r.get("codex_uncached", 0), r.get("wall_s"), r.get("rc"),
                        " CENSORED" if r.get("censored") else
                        (" TOUCHED-TESTS!" if r.get("touched_tests") else "")), flush=True)
                results.append(r); persist()
            reset_to_base()
            _, head_now = g("rev-parse", "--short", "HEAD")
            if head_now != base:
                sys.exit("ABORT: fixture repo failed to reset to base after {0} rep{1}".format(tid, rep))

    if args.dry_run:
        print("\nDRY RUN complete — {0} prompts + notes built. Inspect {1}.".format(len(results), run_path))
        return

    # ---- aggregate + pre-registered gate (pooled over all rep-runs) ----
    def arm_rows(arm):
        return [r for r in results
                if r.get("arm") == arm and "green" in r and not r.get("censored")]
    A, B = arm_rows("A"), arm_rows("B")
    n_censored = sum(1 for r in results if r.get("censored"))
    n_error = sum(1 for r in results if "error" in r)
    n_integrity = sum(1 for r in results if r.get("touched_tests"))
    expected = len(admitted) * args.reps

    def invalid(msg, extra=None):
        v = "INVALID (" + msg + ")"
        print("\n== KG-3 result ==\n  VERDICT:", v)
        verdict_path.write_text(json.dumps(dict(dict(verdict=v, n_censored=n_censored,
            n_error=n_error, integrity_violations=n_integrity), **(extra or {})), indent=2),
            encoding="utf-8")

    if len(A) != expected or len(B) != expected:
        return invalid("incomplete arms: A {0}/{2}, B {1}/{2}; censored={3}, errors={4}".format(
            len(A), len(B), expected, n_censored, n_error))

    # Empty-note floor (review #2): if arm B largely collapsed to arm A (SLM/diagnostician
    # failure) the pooled ε₁≈0 would masquerade as a real KILL. Pre-registered floor: >=90%
    # of scored B-runs must carry a note, else the run measures nothing -> INVALID.
    note_cov = sum(1 for r in B if r.get("note_present")) / len(B)
    if note_cov < 0.90:
        return invalid("note coverage {0:.0%} < 90% — arm B collapsed to arm A "
                       "(diagnostician failure), nothing measured".format(note_cov),
                       dict(note_coverage=round(note_cov, 3)))

    def tpg(rows):
        green = sum(1 for r in rows if r["green"])
        tok = sum(r["codex_uncached"] for r in rows)
        return green, tok, (tok / green if green else float("inf"))
    gA, tA, pA = tpg(A)
    gB, tB, pB = tpg(B)
    if gA == 0 or gB == 0:                    # ratio undefined; degenerate, not "borderline"
        return invalid("an arm scored zero greens (A={0}, B={1}); ε₁ undefined".format(gA, gB))
    slm_tot = sum(r.get("slm_in", 0) + r.get("slm_out", 0) for r in B)

    def eps_of(pa, pb):
        return (1 - pb / pa) if pa not in (0, float("inf")) else float("nan")
    eps = eps_of(pA, pB)                      # PRIMARY: codex-uncached only
    # Secondary (reported, not gated): all-in 1:1 and cost-weighted 0.02x SLM.
    eps_allin = eps_of(pA, (tB + slm_tot) / gB if gB else float("inf"))
    eps_cw = eps_of(pA, (tB + 0.02 * slm_tot) / gB if gB else float("inf"))

    # per-task paired win-rate (mean B-uncached vs mean A-uncached within task)
    tasks = sorted({r["task"] for r in A})
    paired = []
    for t in tasks:
        au = [r["codex_uncached"] for r in A if r["task"] == t]
        bu = [r["codex_uncached"] for r in B if r["task"] == t]
        ma, mb = sum(au) / len(au), sum(bu) / len(bu)
        paired.append(dict(task=t, mean_A=round(ma), mean_B=round(mb),
                           ratio=round(ma / max(1, mb), 3), B_wins=(mb < ma)))
    b_win_tasks = sum(1 for p in paired if p["B_wins"])

    # Gappy-evidence subgroup (review #10 / charter): the hypothesis predicts the note earns on
    # hard-localize (gappy) tasks, not crisp ones. Label via the FROZEN module-mismatch signal.
    import kg1_5_triage as frozen
    gappy_ids = {a["id"] for a in admitted if frozen.sig_module_mismatch(tasks_by_id[a["id"]])}

    def subgroup(ids):
        Ar = [r for r in A if r["task"] in ids]
        Br = [r for r in B if r["task"] in ids]
        _, _, pa = tpg(Ar)
        _, _, pb = tpg(Br)
        return dict(n=len(ids), eps1=eps_of(pa, pb))
    sg_gappy = subgroup(gappy_ids)
    sg_crisp = subgroup(set(tasks) - gappy_ids)

    print("\n== KG-3 result (reps={0}, {1} tasks, note-cov {2:.0%}) ==".format(
        args.reps, len(admitted), note_cov))
    print("  A (raw+pin): green {0}/{1}  codex-uncached tok/green={2:,.0f}".format(gA, len(A), pA))
    print("  B (+note)  : green {0}/{1}  codex-uncached tok/green={2:,.0f}  (+{3:,} SLM tok)".format(
        gB, len(B), pB, slm_tot))
    print("  ε₁ PRIMARY (codex-only) = {0:+.3f}   [all-in {1:+.3f} | cost-weighted {2:+.3f}]".format(
        eps, eps_allin, eps_cw))
    print("  subgroup ε₁: gappy(n={0}) {1:+.3f} | crisp(n={2}) {3:+.3f}   per-task B-wins {4}/{5}".format(
        sg_gappy["n"], sg_gappy["eps1"], sg_crisp["n"], sg_crisp["eps1"], b_win_tasks, len(tasks)))
    if gB < gA:
        verdict = "KILL (note regressed correctness: green {0} < {1})".format(gB, gA)
    elif eps >= 0.10 and gB >= gA:
        verdict = "CONTINUE (codex-only ε₁ ≥ 0.10 at green-parity)"
    elif eps <= 0:
        verdict = "KILL (codex-only ε₁ ≤ 0)"
    else:
        verdict = "REPLICATE (0 < codex-only ε₁ < 0.10)"
    print("  VERDICT:", verdict)
    verdict_path.write_text(json.dumps(dict(
        reps=args.reps, green_A=gA, green_B=gB, tpg_A=pA, tpg_B=pB,
        eps1_primary_codex_only=eps, eps1_allin=eps_allin, eps1_cost_weighted=eps_cw,
        subgroup_gappy=sg_gappy, subgroup_crisp=sg_crisp,
        per_task_B_wins=b_win_tasks, n_tasks=len(tasks), paired=paired, slm_total=slm_tot,
        note_coverage=round(note_cov, 3),
        n_censored=n_censored, n_error=n_error, integrity_violations=n_integrity,
        gated_metric="codex-uncached only, pooled over rep-runs", verdict=verdict), indent=2),
        encoding="utf-8")


if __name__ == "__main__":
    main()
