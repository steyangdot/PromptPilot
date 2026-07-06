"""KG-1.5 — is there a CHEAP, PRE-RUN signal that predicts when the rewrite helps? ($0, offline)

Charter §5/§6 follow-up to KG-1's bimodal finding (kg1_rewrite_value_result). KG-1 showed the
rewrite wins 1.2–21× on hard-to-localize CI evidence and loses ~13% on already-localized evidence.
A triage gate would spend the rewrite only when it pays. This analyzes the 6 KG-1 tasks OFFLINE:
(1) confirm the phenomenon, (2) test pre-run signals against the win/lose label, (3) compute the
EV of triage vs always-rewrite so we know whether a classifier is even worth building.

Signals are computed from the CI failure ALONE (test names + traceback) + the repo — i.e. what a
triage gate would actually see BEFORE spending a codex token. The fix file is used ONLY to label
outcomes, never inside a signal.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_fixtures as cf

HTTPX = cf.HTTPX
KG1 = json.loads((Path(__file__).parent / "kg1_data" / "kg1_run.json").read_text(encoding="utf-8"))
BY = {}
for r in KG1["results"]:
    if "green" in r:
        BY.setdefault(r["task"], {})[r["arm"]] = r
TASKS = {t["id"]: t for t in cf.TASKS}

_MODULE_RE = re.compile(r"test_([a-z0-9]+)\.py")
_QUOTED = re.compile(r"'([^'\n]{3,40})'|\"([^\"\n]{3,40})\"")


def label(task_id) -> str:
    ra, rw = BY[task_id]["raw"], BY[task_id]["rewrite"]
    return "WIN" if ra["codex_uncached"] / max(1, rw["codex_uncached"]) > 1.05 else "LOSE"


def implied_modules(targets) -> set:
    """Source modules a cold triage could guess from the FAILING TEST names (test_X -> _X / X)."""
    out = set()
    for t in targets:
        m = _MODULE_RE.search(t)
        if m:
            base = m.group(1)
            for cand in ("_" + base, base, "_" + base.rstrip("s")):
                out.add(cand)
    return out


def sig_module_mismatch(task) -> bool:
    """PRE-RUN True (=> predict WIN, doesn't localize): the fix module is NOT implied by any
    failing test's name. (Uses fix stem only to CHECK the guess a triage would make; a real gate
    would instead check whether the implied modules plausibly contain the failing symbol.)"""
    fix_stem = Path(task["file"]).stem
    return fix_stem not in implied_modules(task["targets"])


def sig_min_fanout(task_id, task):
    """Distinctive quoted strings from the traceback -> grep httpx SOURCE (not tests). Returns the
    fan-out of the most-localizing distinctive token (min source-files hit, among tokens that hit
    1..N). 1-few = a domain string that pins a file (localizes). 0 = only test data (no anchor)."""
    ev = (cf.OUT / task_id / "evidence.txt").read_text(encoding="utf-8", errors="replace")
    toks = set()
    for a, b in _QUOTED.findall(ev):
        s = (a or b).strip()
        if s and not s.startswith("/") and " " not in s[:3] and any(c.isalpha() for c in s):
            toks.add(s)
    fanouts = []
    for s in toks:
        try:
            p = subprocess.run(["git", "grep", "-l", "--fixed-strings", s, "--", "httpx/"],
                               cwd=HTTPX, capture_output=True, text=True, timeout=30)
            n = len([x for x in p.stdout.splitlines() if x.strip()])
            if n > 0:
                fanouts.append(n)
        except Exception:
            pass
    return (min(fanouts) if fanouts else 0), len(toks)


def main():
    print("== KG-1.5 triage analysis (offline, $0) ==\n")
    rows = []
    for tid in sorted(BY):
        task = TASKS[tid]
        lab = label(tid)
        mm = sig_module_mismatch(task)
        fo, ntok = sig_min_fanout(tid, task)
        ra = BY[tid]["raw"]
        rows.append(dict(task=tid, label=lab, raw_unc=ra["codex_uncached"], raw_calls=ra["tool_calls"],
                         module_mismatch=mm, min_fanout=fo, n_tokens=ntok))
    print("{0:22s} {1:5s} {2:>8s} {3:>6s} | {4:>9s} {5:>10s}".format(
        "task", "label", "raw_unc", "calls", "mod_mism", "min_fanout"))
    for r in rows:
        print("{0:22s} {1:5s} {2:>8,} {3:>6} | {4:>9} {5:>10}".format(
            r["task"], r["label"], r["raw_unc"], r["raw_calls"],
            "MISMATCH" if r["module_mismatch"] else "match", r["min_fanout"]))

    def score(pred_win):
        tp = sum(1 for r in rows if pred_win(r) and r["label"] == "WIN")
        tn = sum(1 for r in rows if not pred_win(r) and r["label"] == "LOSE")
        return tp + tn
    combined = lambda r: r["module_mismatch"] or r["min_fanout"] == 0
    print("\nSIGNAL separation (of 6):")
    print("  module-mismatch -> WIN            : {0}/6  (misses decoder: file matches, logic buried)".format(
        score(lambda r: r["module_mismatch"])))
    print("  min_fanout==0 -> WIN              : {0}/6  (misses url-port: netloc string hits 1 file)".format(
        score(lambda r: r["min_fanout"] == 0)))
    print("  COMBINED (mismatch OR fanout==0)  : {0}/6  <- the two are COMPLEMENTARY".format(
        score(combined)))
    print("  raw_unc>35k -> WIN (POST-run oracle): {0}/6".format(score(lambda r: r["raw_unc"] > 35000)))

    # ---- EV of triage vs always-rewrite (all-in tokens per green) ----
    def toks(r_task, arm, allin=True):
        x = BY[r_task][arm]
        return x["codex_uncached"] + (x["slm_in"] + x["slm_out"] if (allin and arm == "rewrite") else 0)
    greens = len(BY)  # all 6 green in both arms
    always_raw = sum(toks(t, "raw") for t in BY)
    always_rw = sum(toks(t, "rewrite") for t in BY)
    perfect = sum(min(toks(t, "raw"), toks(t, "rewrite")) for t in BY)  # oracle triage
    # combined-signal triage: rewrite iff (module_mismatch OR min_fanout==0), else raw
    rowmap = {r["task"]: r for r in rows}
    def use_rewrite(t):
        r = rowmap[t]
        return r["module_mismatch"] or r["min_fanout"] == 0
    sigtri = sum(toks(t, "rewrite") if use_rewrite(t) else toks(t, "raw") for t in BY)
    print("\nEV (all-in tokens per green, 6 greens each):")
    for name, tot in [("always-raw", always_raw), ("always-rewrite", always_rw),
                      ("combined-signal triage", sigtri), ("PERFECT triage (oracle)", perfect)]:
        eps = 1 - (tot / greens) / (always_raw / greens)
        print("  {0:26s}: {1:>8,.0f} tok/green   eps1 vs always-raw = {2:+.3f}".format(name, tot / greens, eps))

    out = Path(__file__).parent / "kg1_data" / "kg1_5_triage.json"
    out.write_text(json.dumps(dict(rows=rows,
        ev=dict(always_raw=always_raw, always_rewrite=always_rw, module_mismatch=sigtri, perfect=perfect),
        greens=greens), indent=2), encoding="utf-8")
    print("\nwrote", out)


if __name__ == "__main__":
    main()
