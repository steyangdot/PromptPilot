"""KG-1.5b — score the FROZEN triage predictions against the KG-1 v2 labels. ($0, offline)

The out-of-sample test this whole protocol was built for: predictions were recorded in commit
07ee460 BEFORE any v2 outcome existed (classifier frozen at blob ea705204 / commit 1736266);
the KG-1 v2 run (kg1_run_c2.json) produced the labels. This scorer joins the two.

Integrity rules (mirror the predictor's):
  * The LABEL RULE is read from the predictions file and must equal the FROZEN rule below —
    labels are never re-derived from a "better" rule after seeing the data.
  * Signals are NOT recomputed (no import of kg1_5_triage): predictions come solely from the
    committed JSON. This scorer cannot leak outcome data into the classifier.
  * Every predicted task is scored or explicitly reported UNSCOREABLE (censored/error/missing
    rows) — never silently dropped.
  * Refuses to overwrite an existing score file without --force.

Also reports the product-relevant EV: codex-uncached tokens-per-green under the triage policy
(rewrite only on predicted-WIN) vs always-raw vs always-rewrite, on the v2 data.

Run:  python research/kg1_5b_score.py [--run kg1_data/kg1_run_c2.json] [--force]
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
PRED_PATH = RESEARCH / "kg1_data" / "kg1_5b_predictions.json"
DEFAULT_RUN = RESEARCH / "kg1_data" / "kg1_run_c2.json"
OUT = RESEARCH / "kg1_data" / "kg1_5b_score.json"

# The FROZEN label rule — must match the predictions file byte-for-byte (set at freeze time).
FROZEN_LABEL_RULE = "WIN iff raw codex_uncached / max(1, rewrite codex_uncached) > 1.05"
# The predictions file's git blob at the freeze commit 07ee460 (review finding 1: the scorer
# must verify the freeze it stamps into the record, same bar as the predictor's classifier pin).
FROZEN_PREDICTIONS_BLOB = "c0b92157d68338dd8659087a15d1ecdd007f8fde"
FROZEN_PREDICTIONS_COMMIT = "07ee460"


def _git(*args) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        sys.exit("git {0} failed (rc={1}): {2}".format(
            " ".join(args), p.returncode, (p.stderr or "").strip()[:200]))
    return p.stdout.strip()


def predictions_provenance() -> dict:
    """The out-of-sample claim rests on the predictions being byte-identical to the
    pre-outcome freeze — verify by git blob identity + clean working tree."""
    rel = "research/kg1_data/kg1_5b_predictions.json"
    head_blob = _git("rev-parse", "HEAD:" + rel)
    if head_blob != FROZEN_PREDICTIONS_BLOB:
        sys.exit("predictions provenance: {0} at HEAD is blob {1}, NOT the frozen {2} "
                 "({3}) — the predictions changed after the freeze; scoring them would be "
                 "post-outcome fitting.".format(rel, head_blob[:12],
                                                FROZEN_PREDICTIONS_BLOB[:12],
                                                FROZEN_PREDICTIONS_COMMIT))
    if _git("status", "--porcelain", "--", rel):
        sys.exit("predictions provenance: {0} has uncommitted changes — revert before "
                 "scoring.".format(rel))
    return dict(file=rel, frozen_blob=FROZEN_PREDICTIONS_BLOB,
                freeze_commit=FROZEN_PREDICTIONS_COMMIT, working_tree_clean=True)


def label_of(raw_row: dict, rw_row: dict) -> str:
    """The frozen rule, applied verbatim: WIN = the rewrite arm was worth spending."""
    return ("WIN" if raw_row["codex_uncached"] / max(1, rw_row["codex_uncached"]) > 1.05
            else "LOSE")


def usable(row: dict | None) -> bool:
    return (row is not None and "green" in row
            and not row.get("censored") and "codex_uncached" in row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(DEFAULT_RUN), help="KG-1 v2 run artifact")
    ap.add_argument("--force", action="store_true", help="overwrite an existing score file")
    ap.add_argument("--allow-partial", action="store_true",
                    help="score an incomplete run (peeking; NEVER for the citable record)")
    args = ap.parse_args()
    if OUT.exists() and not args.force:
        sys.exit("{0} exists — the out-of-sample score is a one-shot record; --force only to "
                 "regenerate from the SAME inputs.".format(OUT))

    prov = predictions_provenance()
    preds = json.loads(PRED_PATH.read_text(encoding="utf-8"))
    if preds["label_rule"] != FROZEN_LABEL_RULE:
        sys.exit("label rule mismatch: predictions file says {0!r} — refusing to score with a "
                 "different rule.".format(preds["label_rule"]))
    run_p = Path(args.run)
    if not run_p.is_absolute():
        run_p = RESEARCH / run_p          # review finding 5: docstring path resolves vs research/
    run = json.loads(run_p.read_text(encoding="utf-8"))
    if run.get("base_commit") != preds["base_commit"]:
        sys.exit("base mismatch: run artifact base {0!r} != predictions base {1!r} — these are "
                 "not the same corpus state.".format(run.get("base_commit"), preds["base_commit"]))
    rows = {(r["task"], r["arm"]): r for r in run["results"]}
    if len(rows) != len(run["results"]):
        sys.exit("duplicate (task, arm) rows in {0} — a hand-merged artifact would be scored "
                 "last-wins silently; resolve the duplicates first.".format(run_p.name))

    scored, unscoreable = [], []
    for p in preds["rows"]:
        tid = p["task"]
        raw, rw = rows.get((tid, "raw")), rows.get((tid, "rewrite"))
        if not (usable(raw) and usable(rw)):
            why = []
            for arm, r in (("raw", raw), ("rewrite", rw)):
                if r is None:
                    why.append(arm + ": missing")
                elif r.get("censored"):
                    why.append(arm + ": censored")
                elif "green" not in r:
                    why.append(arm + ": error row")
            unscoreable.append(dict(task=tid, predict=p["predict"], why="; ".join(why)))
            continue
        lab = label_of(raw, rw)
        ratio = raw["codex_uncached"] / max(1, rw["codex_uncached"])
        scored.append(dict(task=tid, predict=p["predict"], label=lab,
                           correct=(p["predict"] == lab), ratio=round(ratio, 4),
                           raw_unc=raw["codex_uncached"], rw_unc=rw["codex_uncached"],
                           rw_slm=rw.get("slm_in", 0) + rw.get("slm_out", 0),
                           module_mismatch=p["module_mismatch"], min_fanout=p["min_fanout"],
                           raw_green=raw["green"], rw_green=rw["green"]))

    n = len(scored)
    # Partial-run guard (review finding 2): the one-shot record must never be burned on a
    # live or incomplete artifact.
    if (unscoreable or n != preds["n_tasks"]) and not args.allow_partial:
        sys.exit("incomplete inputs: {0}/{1} tasks scoreable ({2} unscoreable) — refusing to "
                 "write the one-shot record. Use --allow-partial only to peek.".format(
                     n, preds["n_tasks"], len(unscoreable)))
    acc = sum(1 for s in scored if s["correct"])
    print("== KG-1.5b out-of-sample score (frozen predictions {0} vs {1}) ==\n".format(
        preds["classifier"].get("last_commit", preds["classifier"].get("commit")),
        Path(args.run).name))
    print("{0:30s} {1:8s} {2:6s} {3:>7s}  {4}".format("task", "PREDICT", "LABEL", "ratio", "ok"))
    for s in scored:
        print("{0:30s} {1:8s} {2:6s} {3:>6.2f}x  {4}".format(
            s["task"], s["predict"], s["label"], s["ratio"], "Y" if s["correct"] else "MISS"))
    for u in unscoreable:
        print("{0:30s} {1:8s} UNSCOREABLE ({2})".format(u["task"], u["predict"], u["why"]))

    # Trivial-baseline comparisons — a classifier must beat these to mean anything.
    base_win = sum(1 for s in scored if s["label"] == "WIN")
    base_lose = n - base_win
    # Individual signals (recorded per prediction row; NOT recomputed).
    sig_mm = sum(1 for s in scored if (s["module_mismatch"]) == (s["label"] == "WIN"))
    sig_fo = sum(1 for s in scored if (s["min_fanout"] == 0) == (s["label"] == "WIN"))
    print("\naccuracy: {0}/{1}   [always-WIN {2}/{1} | always-LOSE {3}/{1} | "
          "mismatch-only {4}/{1} | fanout-only {5}/{1}]".format(
              acc, n, base_win, base_lose, sig_mm, sig_fo))

    # EV on the PRIMARY metric (codex-uncached only, per the 2026-07-05 amendment); the SLM
    # spend each policy incurs is reported alongside (review finding 3: measured and shown,
    # never hidden — triage spends SLM on predicted-WIN tasks, always-rewrite on all).
    def tokens(policy):
        tot = green = slm = 0
        for s in scored:
            use_rw = (policy == "rewrite") or (policy == "triage" and s["predict"] == "WIN")
            tot += s["rw_unc"] if use_rw else s["raw_unc"]
            slm += s["rw_slm"] if use_rw else 0
            green += 1 if (s["rw_green"] if use_rw else s["raw_green"]) else 0
        return tot, green, slm
    print("\nEV (PRIMARY metric = codex-uncached tok/green; SLM shown as its own line):")
    ev = dict(metric="codex-uncached only (SLM reported separately, never folded in)")
    for pol in ("raw", "rewrite", "triage"):
        tot, green, slm = tokens(pol)
        tpg = tot / green if green else float("inf")
        ev[pol] = dict(total=tot, greens=green, tok_per_green=round(tpg, 1), slm_total=slm)
        print("  {0:8s}: {1:>9,} codex tok, {2}/{3} green, {4:>9,.0f} tok/green  "
              "(+ {5:>7,} SLM tok)".format(pol, tot, green, n, tpg, slm))

    out_path = OUT.with_name("kg1_5b_score_PEEK.json") if args.allow_partial else OUT
    out_path.write_text(json.dumps(dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        run_artifact=run_p.name, run_base_commit=run.get("base_commit"),
        predictions_provenance=prov,
        classifier=preds["classifier"], label_rule=FROZEN_LABEL_RULE,
        n_scored=n, n_unscoreable=len(unscoreable), accuracy=acc,
        allow_partial=bool(args.allow_partial),
        baselines=dict(always_win=base_win, always_lose=base_lose,
                       mismatch_only=sig_mm, fanout_only=sig_fo),
        rows=scored, unscoreable=unscoreable, ev=ev), indent=2), encoding="utf-8")
    print("\nwrote", out_path)


if __name__ == "__main__":
    main()
