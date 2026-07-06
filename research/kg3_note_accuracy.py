"""KG-3 note accuracy — reproducible provenance for the SHIPPING diagnostician. ($0-class, SLM)

Review finding: the charter's "12/16" file-hit for the dedicated `kg3_note.build_note`
diagnostician (as opposed to step-0b's 8/9, which scored the different slm-openai-v2 rewrite
path) had no committed artifact. This script produces it: for each admitted task it runs the
SHIPPING note builder on the committed-seed clean tree and scores whether the note's grep-
verified file equals the known seed file (label only). Writes kg1_data/kg3_note_accuracy.json.

Run:  PYTHONPATH=<worktree> python research/kg3_note_accuracy.py
Cost: ~16 gpt-5.4-nano calls; $0-class, no codex.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "kg1_data" / "kg3_note_accuracy.json"


def _load_env():
    for cand in (_ROOT / ".env", Path("B:/LLM/.env")):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def main():
    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ABORT: OPENAI_API_KEY not set.")
    import ci_fixtures as cf
    from kg3_note import build_note
    import kg1_5_triage as frozen

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]
    base = manifest["base_commit"]
    tasks_by_id = {t["id"]: t for t in cf.TASKS}

    def g(*a, timeout=60):
        rc, out = cf.sh(["git", *a], timeout=timeout)
        return rc, out.strip()

    def reset_to_base():
        g("reset", "--hard", base); g("clean", "-fdx", "--quiet")

    rc, head = g("rev-parse", "--short", "HEAD")
    if head != base:
        _, msg = g("log", "-1", "--format=%s"); _, parent = g("rev-parse", "--short", "HEAD~1")
        if msg.startswith("seed: ") and parent == base:
            reset_to_base()
        else:
            sys.exit("ABORT: fixture repo at {0}, base {1}".format(head, base))

    rows = []
    for a in admitted:
        tid = a["id"]
        task = tasks_by_id[tid]
        fix_name = Path(task["file"]).name          # LABEL only — never fed to the SLM
        reset_to_base()
        cf.apply_edits(task)
        if task["edits"]:
            g("-c", "user.name=kg3", "-c", "user.email=kg3@local",
              "commit", "-am", "seed: {0} (throwaway)".format(tid))
        evidence = (cf.OUT / tid / "evidence.txt").read_text(encoding="utf-8", errors="replace")
        nb = build_note(evidence, cf.HTTPX, lambda p: g("cat-file", "-e", "HEAD:" + p)[0] == 0)
        reset_to_base()
        file_hit = bool(nb["file"]) and Path(nb["file"]).name == fix_name
        gappy = frozen.sig_module_mismatch(task)
        rows.append(dict(task=tid, corpus=a.get("corpus", 1), fix_file=task["file"], gappy=gappy,
                         note_file=nb["file"], file_hit=file_hit,
                         has_mechanism=bool(nb["mechanism"]), mechanism=nb["mechanism"][:200]))
        print("  {0:30s} file={1:<22s} hit={2!s:5} mech={3!s:5}".format(
            tid, str(nb["file"]), file_hit, bool(nb["mechanism"])), flush=True)

    n = len(rows)
    hits = sum(1 for r in rows if r["file_hit"])
    mech = sum(1 for r in rows if r["has_mechanism"])
    gp = [r for r in rows if r["gappy"]]
    gp_hits = sum(1 for r in gp if r["file_hit"])
    print("\n== KG-3 shipping-diagnostician accuracy ==")
    print("  file-hit:        {0}/{1}".format(hits, n))
    print("  gappy file-hit:  {0}/{1}".format(gp_hits, len(gp)))
    print("  has-mechanism:   {0}/{1}".format(mech, n))
    OUT.write_text(json.dumps(dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        base_commit=base, note_builder="kg3_note.build_note (gpt-5.4-nano diagnostician)",
        n=n, file_hits=hits, gappy_file_hits=[gp_hits, len(gp)], has_mechanism=mech,
        rows=rows), indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
