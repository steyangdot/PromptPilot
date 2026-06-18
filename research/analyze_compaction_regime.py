"""
analyze_compaction_regime.py — measurement core for the COMPACTION-REGIME TEST.

Design: docs/COMPACTION_REGIME_TEST.md

Two data sources (the codex CLI splits the information across them):
  * Harness `--json` stdout files (`run{R}_{arm}_t{T}.jsonl`, saved by the harness):
    one `turn.completed.usage{input,cached,output,reasoning}` per turn  -> per-TURN COST,
    plus `thread.started.thread_id`. No per-call detail / window / compaction here.
  * Codex rollouts (`~/.codex/sessions/**/rollout-*-<thread_id>.jsonl`): per-CALL
    `last_token_usage`, `model_context_window`, and compaction events -> per-CALL
    OCCUPANCY + COMPACTION TIMELINE. Matched to runs via the thread_id from the
    harness files; the resumed `builtin` thread is segmented back into turns by the
    per-turn boundary events.

Computes (design):
  - per-CALL occupancy + whether it crosses ~233k (the quantity compaction fires on, C1);
  - compaction timeline -> per-run first-compaction turn -> IN-REGIME turns;
  - per-turn cost (from the harness turn.completed.usage);
  - PRIMARY: marginal in-regime per-turn ratio (builtin/with_session) + 95% CI + straddle rule;
  - SECONDARY: cumulative total-token ratio;
  - validity gate: compaction fired in >= K of N builtin runs.

Read-only: no model calls, does not run the chain.

Usage:
    python analyze_compaction_regime.py [OUT_DIR] [SESSIONS_DIR]
    # OUT_DIR default     = research/data/chain_results_v2/codex/chain_long
    # SESSIONS_DIR default = ~/.codex/sessions
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WINDOW = 258_400
COMPACTION_THRESHOLD = int(WINDOW * 0.90)  # ~232,560
T_HOLD = 1.5
T_NARROW = 1.1
MIN_FIRING_RUNS = 4
# Rollout event types that mark a new turn / resume invocation (try in order).
BOUNDARY_TYPES = ["task_started", "user_message", "turn.started", "turn_context"]


def find_key(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield v
            else:
                yield from find_key(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from find_key(v, key)


def primary_type(o):
    pl = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    return pl.get("type") or o.get("type")


# ---------- source A: harness per-turn stdout ----------
def harness_turn(path: Path) -> dict:
    """per-turn cost (turn.completed.usage) + thread_id from a harness stdout file."""
    cost = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
    tid = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "thread.started" and o.get("thread_id"):
                tid = o["thread_id"]
            if o.get("type") == "turn.completed":
                u = o.get("usage") or {}
                cost = {
                    "input": u.get("input_tokens") or 0,
                    "cached": u.get("cached_input_tokens") or 0,
                    "output": u.get("output_tokens") or 0,
                    "reasoning": u.get("reasoning_output_tokens") or 0,
                }
    cost["total"] = cost["input"] + cost["output"] + cost["reasoning"]
    cost["thread_id"] = tid
    return cost


# ---------- source B: codex rollouts (per-call occupancy + compaction) ----------
def index_rollouts(sessions_dir: Path) -> list:
    """All rollout file paths under sessions_dir (filenames carry the thread_id)."""
    paths = []
    if not sessions_dir.exists():
        return paths
    for root, _dirs, files in os.walk(sessions_dir):
        for fn in files:
            if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                paths.append(Path(root) / fn)
    return paths


def find_rollout(tid: str, rollout_paths: list):
    if not tid:
        return None
    for p in rollout_paths:
        if tid in p.name:
            return p
    return None


def parse_rollout_segmented(path: Path) -> dict:
    """Return {'segments': [ {occ, comp} per turn ], 'window': int|None}.

    Segments are split on the per-turn boundary event (a resumed thread holds all
    turns in one rollout). Picks whichever BOUNDARY_TYPE actually appears.
    """
    # pick a boundary type that occurs in this file
    counts = defaultdict(int)
    window = None
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            pt = primary_type(o)
            if pt:
                counts[pt] += 1
            occ = None
            for lt in find_key(o, "last_token_usage"):
                if isinstance(lt, dict) and lt.get("input_tokens") is not None:
                    occ = max(occ or 0, lt["input_tokens"])
            for w in find_key(o, "model_context_window"):
                if w:
                    window = w
            is_compact = bool(pt and "compact" in pt.lower())
            rows.append((pt, occ, is_compact))
    boundary = next((b for b in BOUNDARY_TYPES if counts.get(b)), None)

    segments = []
    cur = None
    for pt, occ, is_compact in rows:
        if boundary and pt == boundary:
            cur = {"occ": 0, "comp": 0}
            segments.append(cur)
        if cur is None:
            # events before the first boundary -> open an implicit first segment
            cur = {"occ": 0, "comp": 0}
            segments.append(cur)
        if occ is not None:
            cur["occ"] = max(cur["occ"], occ)
        if is_compact:
            cur["comp"] += 1
    return {"segments": segments, "window": window, "boundary": boundary}


def ci95(xs):
    if len(xs) < 2:
        return 0.0
    return 1.96 * statistics.stdev(xs) / math.sqrt(len(xs))


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).parent / "data" / "chain_results_v2" / "codex" / "chain_long")
    sessions_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path(os.path.expanduser("~")) / ".codex" / "sessions")
    if not out_dir.exists():
        raise SystemExit("OUT_DIR not found: {0}".format(out_dir))

    # discover harness files
    pat = re.compile(r"^run(\d+)_(.+)_t(\d+)\.jsonl$")
    harness = defaultdict(lambda: defaultdict(dict))  # arm -> run -> turn -> path
    for p in out_dir.glob("run*_t*.jsonl"):
        m = pat.match(p.name)
        if m:
            harness[m.group(2)][int(m.group(1))][int(m.group(3))] = p
    if not harness:
        raise SystemExit("No run*_<arm>_t*.jsonl files in {0}".format(out_dir))

    rollout_paths = index_rollouts(sessions_dir)

    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 72)
    out("COMPACTION-REGIME ANALYSIS")
    out("  out_dir:      {0}".format(out_dir))
    out("  sessions_dir: {0}  ({1} rollouts indexed)".format(sessions_dir, len(rollout_paths)))
    out("  window={0:,}  compaction threshold (~90%)={1:,}".format(WINDOW, COMPACTION_THRESHOLD))
    out("=" * 72)

    # cost[arm][run][turn], occ[arm][run][turn], comp[arm][run][turn]
    cost = defaultdict(lambda: defaultdict(dict))
    occ = defaultdict(lambda: defaultdict(dict))
    comp = defaultdict(lambda: defaultdict(dict))
    windows = set()
    rollout_misses = 0

    for arm, runs in harness.items():
        for run, turns in runs.items():
            # cost + thread ids from harness
            tids = {}
            for t, p in turns.items():
                h = harness_turn(p)
                cost[arm][run][t] = h["total"]
                tids[t] = h["thread_id"]
            # occupancy + compaction from rollouts
            uniq = set(v for v in tids.values() if v)
            if len(uniq) == 1:
                # one shared thread (resumed builtin): segment one rollout into turns
                tid = next(iter(uniq))
                rp = find_rollout(tid, rollout_paths)
                if rp:
                    seg = parse_rollout_segmented(rp)
                    if seg["window"]:
                        windows.add(seg["window"])
                    segs = seg["segments"]
                    for t in sorted(turns):
                        s = segs[t - 1] if t - 1 < len(segs) else None
                        occ[arm][run][t] = s["occ"] if s else None
                        comp[arm][run][t] = s["comp"] if s else 0
                else:
                    rollout_misses += 1
                    for t in turns:
                        occ[arm][run][t] = None
                        comp[arm][run][t] = 0
            else:
                # fresh thread per turn (with_session/slm_native): one rollout each
                for t in sorted(turns):
                    rp = find_rollout(tids.get(t), rollout_paths)
                    if rp:
                        seg = parse_rollout_segmented(rp)
                        if seg["window"]:
                            windows.add(seg["window"])
                        merged_occ = max((s["occ"] for s in seg["segments"]), default=0)
                        merged_comp = sum(s["comp"] for s in seg["segments"])
                        occ[arm][run][t] = merged_occ
                        comp[arm][run][t] = merged_comp
                    else:
                        rollout_misses += 1
                        occ[arm][run][t] = None
                        comp[arm][run][t] = 0

    out("windows logged in rollouts: {0}".format(sorted(windows) or ["<none — rollouts not matched>"]))
    if rollout_misses:
        out("WARNING: {0} thread(s) had no matching rollout in {1} — occupancy/compaction "
            "unavailable for those (cost metrics still valid). Pass the right SESSIONS_DIR.".format(
                rollout_misses, sessions_dir))
    out("")

    # ---- per-arm curves + compaction timeline ----
    builtin_first_compaction = {}
    for arm in sorted(cost.keys()):
        out("-" * 72)
        out("ARM: {0}".format(arm))
        for run in sorted(cost[arm].keys()):
            turns = sorted(cost[arm][run].keys())
            occs = [occ[arm][run].get(t) for t in turns]
            comps = [comp[arm][run].get(t, 0) for t in turns]
            costs = [cost[arm][run][t] for t in turns]
            peak = max((o for o in occs if o is not None), default=None)
            crossed = next((t for t in turns if (occ[arm][run].get(t) or 0) >= COMPACTION_THRESHOLD), None)
            firstc = next((t for t in turns if comp[arm][run].get(t, 0) > 0), None)
            if arm == "builtin":
                builtin_first_compaction[run] = firstc
            out("  run{0}: peak_occupancy={1}  crossed~233k@T{2}  firstCompaction@T{3}".format(
                run, "{:,}".format(peak) if peak is not None else "?",
                crossed or "-", firstc or "-"))
            out("        per-turn cost:   {0}".format([("{:,}".format(c)) for c in costs]))
            out("        per-turn occ:    {0}".format(["{:,}".format(o) if o is not None else "?" for o in occs]))
            out("        per-turn compact:{0}".format(comps))
        out("")

    # ---- validity gate ----
    out("=" * 72)
    out("VALIDITY GATE")
    n_builtin = len(cost.get("builtin", {}))
    fired = [r for r, t in builtin_first_compaction.items() if t is not None]
    out("  builtin runs={0}  compaction fired in {1}/{0}  (gate requires >= {2})".format(
        n_builtin, len(fired), MIN_FIRING_RUNS))
    regime_valid = len(fired) >= MIN_FIRING_RUNS
    if not regime_valid:
        out("  >>> INVALID / INVESTIGATE: not enough builtin runs compacted.")
        out("      If peak occupancy never reaches ~233k: extend/heavy-up chain_long_fixture.py")
        out("      (this is the calibration-pilot signal). If occupancy crosses but no event")
        out("      fires: suspect #16033 / a config override.")
    out("")

    # ---- PRIMARY: marginal in-regime per-turn ratio ----
    out("=" * 72)
    out("PRIMARY METRIC — marginal in-regime per-turn ratio (builtin / with_session)")
    if "builtin" not in cost or "with_session" not in cost:
        out("  need BOTH builtin and with_session arms.")
    elif not fired:
        out("  no in-regime turns (no compaction). Cannot compute — see validity gate.")
    else:
        ratios, by_turn = [], defaultdict(list)
        for run, f in builtin_first_compaction.items():
            if f is None or run not in cost["with_session"]:
                continue
            for t in sorted(cost["builtin"][run]):
                if t < f:
                    continue
                b = cost["builtin"][run][t]
                w = cost["with_session"][run].get(t)
                if w:
                    ratios.append(b / w)
                    by_turn[t].append(b / w)
        if ratios:
            mean, half = statistics.mean(ratios), ci95(ratios)
            lo, hi = mean - half, mean + half
            out("  in-regime turn-pairs n={0}".format(len(ratios)))
            out("  marginal ratio = {0:.2f}x  95% CI ~ [{1:.2f}, {2:.2f}]".format(mean, lo, hi))
            for t in sorted(by_turn):
                out("    T{0}: {1:.2f}x (n={2})".format(t, statistics.mean(by_turn[t]), len(by_turn[t])))
            if (lo < T_HOLD < hi) or (lo < T_NARROW < hi):
                v = "STRADDLE — CI crosses a threshold; report the band, do NOT call it (§2.3)."
            elif lo >= T_HOLD:
                v = "THESIS HOLDS (>= {0}x).".format(T_HOLD)
            elif lo >= T_NARROW:
                v = "THESIS NARROWS ({0}-{1}x).".format(T_NARROW, T_HOLD)
            else:
                v = "THESIS REFUTED for long sessions (<= {0}x).".format(T_NARROW)
            out("  VERDICT (tokens): {0}".format(v))
            if not regime_valid:
                out("  (!) validity gate failed — provisional.")
        else:
            out("  no matched in-regime turn pairs.")
    out("")

    # ---- SECONDARY: cumulative total ratio ----
    out("=" * 72)
    out("SECONDARY — cumulative total-token ratio (contaminated by sub-threshold turns, §C2):")
    def total(arm):
        return sum(cost[arm][r][t] for r in cost.get(arm, {}) for t in cost[arm][r])
    if "builtin" in cost and "with_session" in cost:
        b, w = total("builtin"), total("with_session")
        out("  builtin={0:,}  with_session={1:,}  ratio={2:.2f}x".format(b, w, (b / w) if w else 0))
    out("")
    out("Lead with TOTAL tokens; uncached is a warmth range and the prefix cache is busted")
    out("at the compaction boundary (§4.5).")

    report = out_dir / "COMPACTION_ANALYSIS.md"
    report.write_text("```\n" + "\n".join(lines) + "\n```\n", encoding="utf-8")
    print("\n[written] {0}".format(report))


if __name__ == "__main__":
    main()
