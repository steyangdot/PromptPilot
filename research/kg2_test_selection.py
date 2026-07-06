"""KG-2 — impacted-test selection: the guard_hits matching re-pointed at the DIFF. ($0, offline)

Charter docs/CAMPAIGN_CI_HEADLESS.md §4/§6. The ledger is retired for CI ("the repo is the
ledger"); what survives of the guard is its file/symbol -> locking-tests matching, fed by the
CHANGE instead of an SLM memory. This module is the prototype AND its pre-registered evaluation.

The selector (two tiers, both mechanical — no SLM, no memory store):
  T1 static   (instant):  name heuristic (httpx/_foo.py -> tests/**/test_foo*.py) UNION
                          symbol-grep (top-level defs/classes of the changed file, git-grepped
                          in tests/) — the direct heir of guard_hits' file/symbol matching.
  T2 coverage (built once per base commit): run the suite once with --cov-context=test, invert
                          to file -> {test files that actually EXECUTE it}. "The repo is the
                          ledger", made literal: ground-truth impact, amortized build cost.

KG-2 evaluation on the admitted CI fixture corpus (each task: known changed file + locking
targets that are RED under the seed):
  catch    = selection includes >=1 target file (a red test runs -> CI goes red; the defect
             cannot escape). Strict-catch = ALL target files selected (full locking evidence).
  wall     = measured pytest runtime of the selected files vs the FULL suite.
Pre-registered bands (charter §6): CONTINUE = 6/6 catch AND mean selected wall <= 0.5x full;
KILL = any escape after at most ONE matcher iteration. ("agent-chooses" comparator deferred —
it costs codex tokens; this gate is the offline full-suite comparison.)

Run:  python research/kg2_test_selection.py [--skip-cov-build]  (uses existing .coverage if present)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ci_fixtures as cf

HTTPX = Path(cf.HTTPX)
PY = sys.executable
OUT = Path(__file__).parent / "kg1_data"          # tracked results dir (shared with KG-1)
COV_CACHE = Path(__file__).parent / "data" / "kg2_cov_cache"   # gitignored (binary db)
FULL_SUITE_TIMEOUT = 900


def sh(cmd, cwd=HTTPX, timeout=300):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------------------
# Tier 1 — static: name heuristic + symbol grep (guard_hits' heir)
# ---------------------------------------------------------------------------
def top_level_symbols(src_file: Path) -> list:
    try:
        tree = ast.parse(src_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not n.name.startswith("__")]


def select_static(changed: str) -> set:
    sel = set()
    stem = Path(changed).stem.lstrip("_")
    for cand in {stem, stem.rstrip("s")}:
        for hit in HTTPX.glob("tests/**/test_{0}*.py".format(cand)):
            sel.add(hit.relative_to(HTTPX).as_posix())
    for sym in top_level_symbols(HTTPX / changed):
        rc, out = sh(["git", "grep", "-l", "--fixed-strings", sym, "--", "tests/"])
        if rc == 0:
            sel.update(x.strip().replace("\\", "/") for x in out.splitlines() if x.strip())
    # TEST files only (review C-F2): the symbol grep also hits tests/conftest.py,
    # tests/common.py etc.; passing conftest.py as a pytest arg ABORTS the run at
    # collection (rc=2) -- the v1 config-timeout-tuple timed row was that abort.
    return {s for s in sel
            if s.endswith(".py") and Path(s).name.startswith("test_")}


# ---------------------------------------------------------------------------
# Tier 2 — coverage map (built once per base commit on the CLEAN tree)
# ---------------------------------------------------------------------------
def changed_lines(task, base_commit: str) -> set:
    """CLEAN-TREE line numbers the seed touches (coverage was measured on the clean tree, so
    these index directly into the map). Edit-based: locate the exact-match anchor. Pre-seeded:
    parse the fixture commit's own -U0 diff."""
    if task.get("base_preseeded"):
        rc, out = sh(["git", "diff", "-U0", "{0}~1..{0}".format(base_commit), "--", task["file"]])
        lines = set()
        for m in re.finditer(r"@@ [^+]*\+(\d+)(?:,(\d+))? @@", out):
            start, cnt = int(m.group(1)), int(m.group(2) or "1")
            lines.update(range(start, start + max(cnt, 1)))
        return {ln for ln in lines if ln >= 1}   # pure-deletion-at-top hunks emit line 0
    src = (HTTPX / task["file"]).read_text(encoding="utf-8")
    lines = set()
    for old, _new in task["edits"]:
        # Mirror apply_edits' admission-time uniqueness contract (review C-F7): a base
        # drift that makes an anchor non-unique must fail loudly, not mislocate lines.
        if src.count(old) != 1:
            raise RuntimeError("anchor not unique ({0}x) for {1}: {2!r}".format(
                src.count(old), task["id"], old[:60]))
        start = src[:src.index(old)].count("\n") + 1
        lines.update(range(start, start + old.count("\n") + 1))
    return lines


def build_coverage_map(base_commit: str, skip_build=False) -> dict | None:
    # The db is CACHED outside the fixture repo (review C-F3: reset_repo's `git clean
    # -fdx` deletes an in-repo .coverage before the old existence check ever saw it,
    # so --skip-cov-build silently rebuilt every time). Cache is keyed by base commit.
    cache = COV_CACHE / "coverage_{0}.db".format(base_commit)
    if skip_build and cache.exists():
        cov_path = cache
        print("[kg2] using cached coverage map: {0}".format(cache), flush=True)
    else:
        cov_file = HTTPX / ".coverage"
        print("[kg2] building coverage map (full suite once, --cov-context=test)...", flush=True)
        cf.reset_repo()
        # bare --cov (NOT --cov=httpx): the repo's own coverage config sets `include`, and
        # httpx promotes warnings to errors — passing a source triggers a fatal
        # include-ignored CoverageWarning and no .coverage is written (first-pass failure).
        rc, out = sh([PY, "-m", "pytest", "tests", "-q", "--no-header",
                      "--cov", "--cov-context=test", "-p", "no:cacheprovider"],
                     timeout=FULL_SUITE_TIMEOUT)
        tail = [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln]
        print("[kg2] coverage suite: {0}".format(tail[-1].strip() if tail else "(no summary)"), flush=True)
        if not cov_file.exists():
            print("[kg2] WARNING: no .coverage produced — tier-2 unavailable")
            return None
        COV_CACHE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cov_file, cache)
        cov_path = cache
    try:
        from coverage import CoverageData
        d = CoverageData(str(cov_path))
        d.read()
        fmap = defaultdict(set)
        linemap = defaultdict(lambda: defaultdict(set))   # relpath -> lineno -> {test files}
        for mf in d.measured_files():
            rel = Path(mf).resolve()
            try:
                relp = rel.relative_to(HTTPX.resolve()).as_posix()
            except ValueError:
                continue
            if not relp.startswith("httpx/"):
                continue
            for lineno, ctxs in (d.contexts_by_lineno(mf) or {}).items():
                for c in ctxs:
                    tf = c.split("::", 1)[0].strip().replace("\\", "/")
                    if tf.startswith("tests/") and tf.endswith(".py"):
                        fmap[relp].add(tf)
                        linemap[relp][lineno].add(tf)
        return {"file": dict(fmap), "line": {k: dict(v) for k, v in linemap.items()}}
    except Exception as e:
        print("[kg2] WARNING: coverage map read failed ({0}) — tier-2 unavailable".format(e))
        return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def timed_pytest(files, timeout=FULL_SUITE_TIMEOUT):
    if not files:
        return 0.0, "(empty selection)"
    t0 = time.time()
    rc, out = sh([PY, "-m", "pytest", *sorted(files), "-q", "--no-header",
                  "-p", "no:cacheprovider"], timeout=timeout)
    tail = [ln for ln in out.splitlines() if " passed" in ln or " failed" in ln]
    return time.time() - t0, (tail[-1].strip() if tail else "rc={0}".format(rc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-cov-build", action="store_true")
    ap.add_argument("--corpus", type=int,
                    help="score only tasks with this corpus tag (1=v1, 2=v2); default all admitted")
    args = ap.parse_args()

    manifest = json.loads((cf.OUT / "manifest.json").read_text(encoding="utf-8"))
    admitted = manifest["admitted"]
    if args.corpus:
        admitted = [a for a in admitted if a.get("corpus", 1) == args.corpus]
        if not admitted:
            sys.exit("no admitted tasks with corpus == {0}".format(args.corpus))
    tasks_by_id = {t["id"]: t for t in cf.TASKS}
    base_commit = manifest["base_commit"]

    # Base guard (review C-F4): coverage line numbers, anchors and timing all assume the
    # manifest's base tree; a drifted fixture repo must abort, not silently mis-map.
    head = sh(["git", "rev-parse", "--short", "HEAD"], timeout=30)[1].strip()
    if head != base_commit:
        sys.exit("fixture repo at {0}, manifest base is {1} -- restore the base first".format(
            head, base_commit))

    cf.reset_repo()
    print("[kg2] timing FULL suite on the clean tree (cold)...", flush=True)
    full_wall, full_sum = timed_pytest(["tests"])
    print("[kg2] full suite: {0:.1f}s  ({1})".format(full_wall, full_sum), flush=True)

    covmap = build_coverage_map(base_commit, skip_build=args.skip_cov_build)
    cf.reset_repo()

    rows = []
    for a in admitted:
        task = tasks_by_id[a["id"]]
        changed = task["file"]
        targets = set(t.replace("\\", "/") for t in a["targets"])
        sel_t1 = select_static(changed)
        fmap = (covmap or {}).get("file", {})
        lmap = (covmap or {}).get("line", {})
        sel_t2f = set(fmap.get(changed, set()))
        # MATCHER ITERATION (the one allowed, spent on precision): line-scoped coverage —
        # only tests whose contexts cover the CHANGED lines. Fallback to file-level if the
        # changed lines have no contexts (keeps iteration-2's proven recall).
        clines = changed_lines(task, base_commit)
        sel_t2l = set()
        for ln in clines:
            sel_t2l |= lmap.get(changed, {}).get(ln, set())
        t2l_fell_back = False
        if covmap and not sel_t2l:
            sel_t2l = sel_t2f
            t2l_fell_back = True
        primary = sel_t1 | sel_t2l          # the final matcher: static ∪ line-scoped coverage
        row = dict(task=a["id"], changed=changed, targets=sorted(targets),
                   changed_lines=sorted(clines), t2_line_fallback=t2l_fell_back)
        for tier, sel in (("t1_static", sel_t1), ("t2_file", sel_t2f),
                          ("t2_line", sel_t2l), ("primary", primary)):
            row[tier] = dict(n=len(sel), catch=bool(sel & targets),
                             strict=targets.issubset(sel), sel=sorted(sel))
        # Cold-cold timing (review C-F5): the full suite paid cold bytecode compilation
        # after a clean -fdx; without a reset here, selected runs 2..n ride run 1's warm
        # __pycache__ — a systematic bias in the selector's favor (~0.05 on the ratio).
        cf.reset_repo()
        wall, summ = timed_pytest(primary)
        row["primary_wall_s"] = round(wall, 1)
        row["primary_summary"] = summ
        rows.append(row)
        print("  {0:22s} t1 n={1:>2} | t2file n={2:>2} | t2line n={3:>2}{4} | PRIMARY n={5:>2} "
              "catch={6!s:5} strict={7!s:5} wall {8:>5.1f}s".format(
                  a["id"], row["t1_static"]["n"], row["t2_file"]["n"], row["t2_line"]["n"],
                  "(fb)" if t2l_fell_back else "    ", row["primary"]["n"],
                  row["primary"]["catch"], row["primary"]["strict"], row["primary_wall_s"]),
              flush=True)

    # ---- gate (judged on the PRIMARY matcher = static ∪ line-scoped cov) ----
    # Bands generalized to n = len(rows) (review C-F1: the v1 code hardcoded 6, which on
    # a 16-task corpus judges "6 catches + 10 escapes" CONTINUE and "16/16" KILL).
    n = len(rows)
    def agg(tier):
        catches = sum(1 for r in rows if r[tier]["catch"])
        stricts = sum(1 for r in rows if r[tier]["strict"])
        mean_n = sum(r[tier]["n"] for r in rows) / n
        return catches, stricts, mean_n
    print("\n== KG-2 result (n={0} tasks, full suite = {1:.1f}s) ==".format(n, full_wall))
    for tier in ("t1_static", "t2_file", "t2_line", "primary"):
        c, s, mn = agg(tier)
        print("  {0:10s}: catch {1}/{2}  strict {3}/{2}  mean selection {4:.1f} files".format(
            tier, c, n, s, mn))
    # Aborted timed runs (pytest rc=2 etc.) must be visible, never averaged in silently.
    aborted = [r["task"] for r in rows if "passed" not in r["primary_summary"]
               and "failed" not in r["primary_summary"]]
    if aborted:
        print("  WARNING: timed run did not execute for: {0}".format(", ".join(aborted)))
    mean_wall = sum(r["primary_wall_s"] for r in rows) / n
    ratio = mean_wall / full_wall if full_wall else float("inf")
    cp, sp, _ = agg("primary")
    print("  PRIMARY mean wall: {0:.1f}s = {1:.2f}x full suite".format(mean_wall, ratio))
    if aborted:
        verdict = "INVALID ({0}/{1} timed selections aborted at collection)".format(len(aborted), n)
    elif cp == n and ratio <= 0.5:
        verdict = "CONTINUE ({0}/{0} catch, wall {1:.2f}x <= 0.5x) [matcher: static UNION line-scoped]".format(n, ratio)
    elif cp < n:
        verdict = "KILL (escape: catch {0}/{1})".format(cp, n)
    else:
        verdict = "KILL (wall {0:.2f}x > 0.5x)".format(ratio)
    print("  VERDICT:", verdict)

    # New label-suffixed artifact names: the published v1 kg2_result.json is an immutable
    # record — never rewritten by later runs (same doctrine as the pinned evidence files).
    label = "c{0}".format(args.corpus) if args.corpus else "all"
    out_path = OUT / "kg2_result_{0}.json".format(label)
    out_path.write_text(json.dumps(dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        n_tasks=n, corpus=args.corpus, base_commit=base_commit,
        timing="cold-cold (reset_repo before the full suite and before every timed selection)",
        full_suite_wall_s=round(full_wall, 1), rows=rows, verdict=verdict,
        matcher="t1_static UNION t2_line_scoped (fallback t2_file); test_*.py files only",
        covmap_available=bool(covmap)), indent=2), encoding="utf-8")
    print("wrote", out_path)


if __name__ == "__main__":
    main()
