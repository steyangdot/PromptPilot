"""Recover/impute the TRUE uncached tokens for timed-out turns and restate the
per-arm uncached headline.

Why this exists: a turn that hit the wall-clock cap recorded 0 uncached tokens in
its saved run file (claude '{}' overwrite; codex parse-before-orphan-flush) while
still earning file-hash success. Simply EXCLUDING those turns (see
analyze_uncached_cost.py) fixes success/$ but NOT the uncached-token ratio,
because the censored turns are already 0 in the sum — you cannot restore missing
tokens by dropping them. The honest correction is:

  - codex: RECOVER. The killed grandchild keeps running and appends a terminal
    `turn.completed` carrying real usage to the raw JSONL. Re-parse the last
    `turn.completed` per turn -> exact recovered uncached. (Caveat: includes
    post-deadline orphan work, so the recovered total is an upper-bound-corrected
    estimate; the true within-budget value lies between recorded and recovered.)

  - claude: IMPUTE. The transcript was overwritten with '{}' and the orphan
    reaped, so the tokens are UNRECOVERABLE. Fill each censored turn with the
    arm's mean uncached over its surviving same-position turns. This is an
    ESTIMATE, noisier the fewer same-position turns survive.

Reproduces the documented corrections:
  codex  seededbug3:     slm_native/with 4.47x -> ~2.36x ; builtin/with 4.99x -> ~2.56x  (recovered)
  claude claude_isolation: slm_native/with 0.38x -> ~0.53x ; builtin clean anchor 46,477/run (imputed)

Usage:
  python research/recover_uncached.py <RUN_DIR> --tool {codex|claude-code} --chain chain1
  e.g.  python research/recover_uncached.py B:/LLM/_session_retest_2026-06-07/seededbug3 --tool codex
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agentic_variety_test import turn_timed_out, _turn_uncached  # noqa: E402

ARMS = ["no_session", "with_session", "slm_native", "stacked", "builtin", "gated_session"]


def _recover_codex_turn_uncached(jsonl_path: Path) -> int | None:
    """Last turn.completed usage in a raw codex JSONL -> uncached, else None."""
    last = None
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") == "turn.completed":
                    last = e.get("usage", {})
    except FileNotFoundError:
        return None
    if not last:
        return None
    return last.get("input_tokens", 0) - last.get("cached_input_tokens", 0)


def _arm_runs(cdir: Path, arm: str) -> list[list[dict]]:
    out = []
    for rf in sorted(glob.glob(str(cdir / f"{arm}_run*.json"))):
        try:
            out.append((rf, json.loads(Path(rf).read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def recover_arm(cdir: Path, arm: str, tool: str) -> dict | None:
    runs = _arm_runs(cdir, arm)
    if not runs:
        return None
    # Two parallel matrices: `recorded` = what the run file stored (~0 for a
    # censored turn) and `corrected` = recovered (codex, from raw JSONL) / actual
    # (clean turn) / None (to be imputed for claude). Keeping them separate avoids
    # polluting the recorded total with recovered values.
    recorded, corrected, recovered_hits = [], [], 0
    for rf, turns in runs:
        rec_row, cor_row = [], []
        rundir = Path(rf).parent
        m = re.match(r"(.+)_run(\d+)\.json$", os.path.basename(rf))
        run_idx = m.group(2) if m else "?"
        for i, t in enumerate(turns):
            rec_row.append(float(_turn_uncached(t)))   # as recorded (~0 if censored)
            if turn_timed_out(t, tool):
                cell = None
                if tool == "codex":
                    turn_no = t.get("turn", i + 1)      # JSONL files are 1-indexed t1..t5
                    jp = rundir / f"run{run_idx}_{arm}_t{turn_no}.jsonl"
                    rec = _recover_codex_turn_uncached(jp)
                    if rec and rec > 0:
                        cell = float(rec)
                        recovered_hits += 1
                cor_row.append(cell)                    # None -> impute below
            else:
                cor_row.append(float(_turn_uncached(t)))
        recorded.append(rec_row)
        corrected.append(cor_row)

    # Impute remaining None cells (claude unrecoverable, or codex turn.failed with
    # no usage) from each arm's surviving same-position corrected values.
    def posmean(pos):
        vals = [r[pos] for r in corrected if pos < len(r) and r[pos] is not None]
        return statistics.mean(vals) if vals else 0.0

    recorded_sum = corrected_sum = 0.0
    imputed_hits = 0
    for ri, cor_row in enumerate(corrected):
        for pos, v in enumerate(cor_row):
            recorded_sum += recorded[ri][pos]
            if v is None:
                corrected_sum += posmean(pos)
                imputed_hits += 1
            else:
                corrected_sum += v
    nr = len(corrected)
    return {
        "n_runs": nr,
        "recorded_per_run": recorded_sum / nr,
        "corrected_per_run": corrected_sum / nr,
        "recovered_turns": recovered_hits,
        "imputed_turns": imputed_hits,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--tool", required=True, choices=["codex", "claude-code"])
    ap.add_argument("--chain", default="chain1")
    args = ap.parse_args()
    cdir = Path(args.run_dir) / args.tool / args.chain
    if not cdir.is_dir():
        print(f"  [skip] {cdir} not found")
        return
    stats = {a: recover_arm(cdir, a, args.tool) for a in ARMS}
    stats = {a: s for a, s in stats.items() if s}
    method = "RECOVERED from raw JSONL" if args.tool == "codex" else "IMPUTED (estimate; claude tokens unrecoverable)"
    print(f"\n  {args.tool} / {args.chain}  -- uncached correction ({method})")
    print("  " + "-" * 78)
    print("  {:<16} {:>14} {:>14} {:>9} {:>9}".format(
        "arm", "recorded/run", "corrected/run", "recov", "imput"))
    for a, s in stats.items():
        print("  {:<16} {:>14,.0f} {:>14,.0f} {:>9d} {:>9d}".format(
            a, s["recorded_per_run"], s["corrected_per_run"],
            s["recovered_turns"], s["imputed_turns"]))
    print("  " + "-" * 78)
    if "with_session" in stats:
        w = stats["with_session"]["corrected_per_run"]
        wr = stats["with_session"]["recorded_per_run"]
        for a in ("slm_native", "builtin"):
            if a in stats and w and wr:
                c = stats[a]["corrected_per_run"] / w
                r = stats[a]["recorded_per_run"] / wr
                print(f"    {a}/with_session uncached: recorded {r:.2f}x -> corrected {c:.2f}x")
    if args.tool == "codex":
        print("    NOTE: recovered totals include post-deadline orphan work, so the")
        print("    corrected ratio is an upper-bound estimate; true value in [corrected, recorded-implied].")
    print()


if __name__ == "__main__":
    main()
