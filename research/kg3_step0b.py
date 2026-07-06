"""KG-3 step-0b — the $0 desk kill-gate: is the SLM's DIAGNOSIS actually correct? (SLM only)

KG-3 (docs/PHASE3_PRPT_CI_DESIGN.md §3 / the parked hypothesis in CAMPAIGN §6 OUTCOMES v2):
append a verified, hedged SLM diagnosis BELOW intact evidence, preserving the rewrite's win
(correct diagnosis saves exploration) while flooring the loss (evidence intact → verification
stays bounded). Its whole ε depends on the diagnosis being RIGHT often enough on the
gappy-evidence subset. This script measures that BEFORE any codex spend:

  For each admitted task: committed-seed clean tree (protocol v2, no diff leak) → run the
  PRODUCTION SLM path (prepare_no_session / slm-openai-v2, gpt-5.4-nano) on the captured
  evidence → extract spec.target_files (location diagnosis) → score file-match against the
  KNOWN seed file (used only to LABEL, never fed to the SLM). Also classify each task
  gappy-vs-crisp via the FROZEN module-mismatch signal, and report the match rate on the
  gappy subset (where KG-3 is supposed to earn).

Kill rule (pre-registered here, before running): if grep-verified location diagnoses are
correct on < ~half the gappy-evidence tasks, KG-3 dies on the desk — the append-only form
cannot beat the coin the KG-1 v2 payoff table implies. Mechanism-quality (downstream_prompt)
is dumped for human inspection alongside the mechanical file-match.

Run:  PYTHONPATH=<worktree> python research/kg3_step0b.py
Cost: ~32 nano SLM calls (classify + rewrite × 16 tasks); $0-class, no codex.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "kg1_data" / "kg3_step0b.json"
DUMP = Path(__file__).parent / "data" / "kg3_step0b"          # gitignored note dumps


def _load_env():
    for cand in (_ROOT / ".env", Path("B:/LLM/.env")):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


_SLM_CALLS = []


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


def main():
    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ABORT: OPENAI_API_KEY not set (needed for the slm-openai-v2 diagnosis).")

    import ci_fixtures as cf
    import chain_test_v2 as ct
    ct._NORMALIZER_NAME = "slm-openai-v2"
    from chain_test_v2 import prepare_no_session
    import kg1_5_triage as frozen           # the FROZEN gappy/crisp signal (labels only)

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]
    base = manifest["base_commit"]
    tasks_by_id = {t["id"]: t for t in cf.TASKS}
    DUMP.mkdir(parents=True, exist_ok=True)

    def g(*a, timeout=60):
        rc, out = cf.sh(["git", *a], timeout=timeout)
        return rc, out.strip()

    def reset_to_base():
        g("reset", "--hard", base); g("clean", "-fdx", "--quiet")

    # base guard
    rc, head = g("rev-parse", "--short", "HEAD")
    if head != base:
        _, msg = g("log", "-1", "--format=%s"); _, parent = g("rev-parse", "--short", "HEAD~1")
        if msg.startswith("seed: ") and parent == base:
            reset_to_base()
        else:
            sys.exit("ABORT: fixture repo at {0}, manifest base {1}".format(head, base))

    if not _install_slm_usage_tap():
        print("[kg3-0b] WARNING: SLM usage tap unavailable — cost unreported", flush=True)

    def norm_name(s):
        s = s.replace("\\", "/")
        return Path(s).name, Path(s).stem.lstrip("_")

    rows = []
    for a in admitted:
        tid = a["id"]
        task = tasks_by_id[tid]
        fix_file = task["file"]                        # LABEL only — never given to the SLM
        reset_to_base()
        cf.apply_edits(task)
        if task["edits"]:
            g("-c", "user.name=kg3", "-c", "user.email=kg3@local",
              "commit", "-am", "seed: {0} (throwaway)".format(tid))
        evidence = (cf.OUT / tid / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        raw = "The tests below are failing in CI. Fix the underlying bug in the library SOURCE " \
              "so the tests pass.\n\n[Failing test output]\n{0}".format(evidence.strip())
        prepared = prepare_no_session(raw, cf.HTTPX, "codex")
        spec = getattr(prepared.get("_normalizer"), "_last_spec", None)
        targets = list(getattr(spec, "target_files", []) or []) if spec else []
        downstream = (getattr(spec, "downstream_prompt", "") or prepared.get("rewrite", ""))[:600]
        reset_to_base()

        # LOCATION score: does any SLM target file name/stem match the true fix file?
        fix_name, fix_stem = norm_name(fix_file)
        tnames = [norm_name(t) for t in targets]
        # grep-verify: the pointer must actually exist in the repo (KG-3's cheap guard)
        verified = []
        for t in targets:
            rc, _ = g("cat-file", "-e", "HEAD:" + t.replace("\\", "/"))
            if rc == 0:
                verified.append(t)
        loc_match = any(n == fix_name or s == fix_stem for n, s in tnames)
        loc_match_verified = any(
            norm_name(t)[0] == fix_name or norm_name(t)[1] == fix_stem for t in verified)
        gappy = frozen.sig_module_mismatch(task)       # True => hard-localize (KG-3's home turf)

        rows.append(dict(task=tid, corpus=a.get("corpus", 1), fix_file=fix_file, gappy=gappy,
                         slm_targets=targets, verified_targets=verified,
                         loc_match=loc_match, loc_match_verified=loc_match_verified))
        (DUMP / (tid + "_note.txt")).write_text(
            "FIX FILE (label): {0}\nGAPPY: {1}\nSLM target_files: {2}\nVERIFIED: {3}\n"
            "LOC MATCH (verified): {4}\n\n--- downstream/mechanism (for human read) ---\n{5}".format(
                fix_file, gappy, targets, verified, loc_match_verified, downstream),
            encoding="utf-8")
        print("  {0:30s} gappy={1!s:5} match={2!s:5} targets={3}".format(
            tid, gappy, loc_match_verified, verified or targets), flush=True)

    # ---- aggregate ----
    n = len(rows)
    gappy = [r for r in rows if r["gappy"]]
    crisp = [r for r in rows if not r["gappy"]]
    def rate(rs): return sum(1 for r in rs if r["loc_match_verified"]), len(rs)
    ov = rate(rows); gp = rate(gappy); cr = rate(crisp)
    slm_in = sum(i for i, _ in _SLM_CALLS); slm_out = sum(o for _, o in _SLM_CALLS)
    print("\n== KG-3 step-0b (verified-location diagnosis correctness) ==")
    print("  overall:          {0}/{1}".format(*ov))
    print("  GAPPY subset:     {0}/{1}   <- KG-3 earns here".format(*gp))
    print("  crisp subset:     {0}/{1}".format(*cr))
    print("  SLM cost: {0:,} in / {1:,} out tok ({2} calls)".format(slm_in, slm_out, len(_SLM_CALLS)))
    kill = gp[1] > 0 and gp[0] / gp[1] < 0.5
    verdict = ("KILL-ON-DESK (gappy-location correctness < 50%: {0}/{1}) — append-only KG-3 "
               "cannot beat the payoff coin; do NOT fund the codex run".format(*gp) if kill
               else "PROCEED (gappy-location correctness {0}/{1} >= 50%) — pre-register the "
                    "repeated-measures KG-3 A/B".format(*gp))
    print("  VERDICT:", verdict)

    OUT.write_text(json.dumps(dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        base_commit=base, n=n, overall_match=ov, gappy_match=gp, crisp_match=cr,
        slm_in=slm_in, slm_out=slm_out, verdict=verdict, rows=rows), indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
