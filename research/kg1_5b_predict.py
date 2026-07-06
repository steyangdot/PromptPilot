"""KG-1.5b — record the FROZEN triage classifier's predictions on the v2 corpus. ($0, offline)

Out-of-sample protocol (the fix for KG-1.5's over-fit caveat): the classifier in
`research/kg1_5_triage.py` was fitted on the 6 v1 tasks and frozen in commit 1736266 BEFORE any
v2 task existed. This script (a) proves the classifier file is unmodified in git, (b) computes
its two signals on the v2 tasks by IMPORTING the frozen functions (never re-implementing them),
and (c) writes the predictions to a tracked JSON **before any KG-1 v2 run produces outcomes**.
Predictions-before-outcomes makes the later accuracy score a real out-of-sample test.

Frozen combined rule (predict WIN = "the rewrite will pay for itself on this task"):
    module_mismatch OR min_fanout == 0
Label rule for later scoring (from frozen kg1_5_triage.label):
    WIN iff raw codex_uncached / rewrite codex_uncached > 1.05

Run:  python research/kg1_5b_predict.py [--force]   (after ci_fixtures.py admits the v2 tasks)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RESEARCH = Path(__file__).parent
ROOT = RESEARCH.parent
sys.path.insert(0, str(RESEARCH))
import ci_fixtures as cf
import kg1_5_triage as frozen  # the classifier frozen at commit 1736266 -- DO NOT MODIFY IT

OUT = RESEARCH / "kg1_data" / "kg1_5b_predictions.json"
# The classifier's git BLOB sha at the freeze commit 1736266. Pinning the file CONTENT (not
# merely "is committed + clean") is what makes the freeze real: any edit -- committed or not --
# changes this hash and voids the prediction. (Review #5: a post-freeze *commit* to the
# classifier passed the old committed+clean check; a blob-identity check cannot be fooled that
# way. Comparing git blob shas is also filter-safe, unlike hashing the working file directly.)
FROZEN_CLASSIFIER_BLOB = "ea705204aca99f7a4af27a5bf765c14be5805479"
FROZEN_CLASSIFIER_COMMIT = "1736266"
COMBINED_RULE = "module_mismatch OR min_fanout == 0  -> predict WIN (use the rewrite)"
LABEL_RULE = "WIN iff raw codex_uncached / max(1, rewrite codex_uncached) > 1.05"


def _git(*args) -> str:
    """Run a git command, ABORTING on failure (review #6: the old unchecked calls let a git
    error that returned empty stdout read as 'clean working tree')."""
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        sys.exit("git {0} failed (rc={1}): {2}".format(
            " ".join(args), p.returncode, (p.stderr or "").strip()[:200]))
    return p.stdout.strip()


def classifier_provenance() -> dict:
    """Prove the classifier is BYTE-IDENTICAL to the frozen 1736266 version, via git blob
    identity (content-pinned, filter-safe) AND a clean working tree (working == HEAD)."""
    rel = "research/kg1_5_triage.py"
    head_blob = _git("rev-parse", "HEAD:" + rel)     # rc!=0 (=> abort) if the path is absent
    if head_blob != FROZEN_CLASSIFIER_BLOB:
        sys.exit("classifier provenance: {0} at HEAD is blob {1}, NOT the frozen {2} ({3}) -- "
                 "the classifier changed since freeze; the out-of-sample guarantee is void.".format(
                     rel, head_blob[:12], FROZEN_CLASSIFIER_BLOB[:12], FROZEN_CLASSIFIER_COMMIT))
    if _git("status", "--porcelain", "--", rel):     # non-empty => working tree differs from HEAD
        sys.exit("classifier provenance: {0} has UNCOMMITTED changes -- working tree differs "
                 "from the frozen blob. Revert before predicting.".format(rel))
    last_commit = _git("log", "-1", "--format=%h", "--", rel)
    return dict(file=rel, frozen_blob=FROZEN_CLASSIFIER_BLOB, head_blob=head_blob,
                last_commit=last_commit, working_tree_clean=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing predictions file (pre-registration guard)")
    args = ap.parse_args()
    if OUT.exists() and not args.force:
        sys.exit("{0} already exists -- predictions are pre-registered and must not be "
                 "silently regenerated. Use --force only if no KG-1 v2 outcome exists yet.".format(OUT))

    prov = classifier_provenance()
    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    v2 = [a for a in manifest["admitted"] if a.get("corpus", 1) == 2]
    if not v2:
        sys.exit("no corpus-v2 tasks in the manifest -- run ci_fixtures.py first")
    tasks = {t["id"]: t for t in cf.TASKS}

    rows = []
    print("== KG-1.5b predictions (frozen classifier @ {0}, blob {1}) ==\n".format(
        prov["last_commit"], prov["frozen_blob"][:12]))
    print("{0:30s} {1:>9s} {2:>10s}  {3}".format("task", "mod_mism", "min_fanout", "PREDICT"))
    for a in v2:
        tid = a["id"]
        task = tasks[tid]
        mm = frozen.sig_module_mismatch(task)
        fo, ntok = frozen.sig_min_fanout(tid, task)
        pred = "WIN" if (mm or fo == 0) else "LOSE"
        rows.append(dict(task=tid, module_mismatch=mm, min_fanout=fo, n_tokens=ntok,
                         predict=pred))
        print("{0:30s} {1:>9s} {2:>10d}  {3}".format(
            tid, "MISMATCH" if mm else "match", fo, pred))

    OUT.write_text(json.dumps(dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        classifier=prov, combined_rule=COMBINED_RULE, label_rule=LABEL_RULE,
        base_commit=manifest["base_commit"], n_tasks=len(rows), rows=rows,
        note="Recorded BEFORE any KG-1 v2 run: no outcome existed for these tasks at "
             "generation time. Score against labels with the frozen label rule only.",
    ), indent=2), encoding="utf-8")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
