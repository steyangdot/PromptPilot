"""
analyze_compaction_regime.py — measurement core for the COMPACTION-REGIME TEST.

Design: docs/COMPACTION_REGIME_TEST.md

Data sources (kept aligned with the rest of the harness — see the PR #43 review):
  * Harness saved per-run JSON (`{arm}_run{R}.json`): the SAME parsed per-turn
    `usage` the harness aggregates, incl. `input_tokens` (= the published "total
    tokens fed", gross / cache-inclusive, output EXCLUDED — BENCHMARKS.md:62),
    `uncached_tokens`, and the `timed_out` / `score.censored` flags. We take
    per-turn COST and the censored flag from here and **exclude censored turns**
    from both metrics (matching `aggregate_runs`/`turn_timed_out`; the guard that
    fixed the 4.47x->2.36x inflation). Re-parsing raw stdout for cost would skip
    this guard and let a timed-out in-regime turn (total~0) bias the ratio.
  * Raw `--json` stdout (`run{R}_{arm}_t{T}.jsonl`): used ONLY for the
    `thread.started.thread_id`, to locate the codex rollouts.
  * Codex rollouts (`~/.codex/sessions/**/rollout-*-<thread_id>.jsonl`): per-CALL
    `last_token_usage` (OCCUPANCY) + `model_context_window` + compaction events.
    The resumed `builtin` thread is segmented back into turns; ALL rollout files
    for a thread are concatenated (a post-compaction rollover can split a thread).

Metrics (design §2):
  - total = gross input_tokens (cache-inclusive) — the SAME total we publish.
  - PRIMARY: marginal in-regime per-turn ratio, aggregated to ONE ratio per run,
    CI over the N runs (turns within a run are correlated — don't pool them).
  - SECONDARY: cumulative total ratio (censored turns excluded).
  - validity gate: compaction fired in >= K of N builtin runs.

Read-only: no model calls, does not run the chain.

Usage:
    python analyze_compaction_regime.py [OUT_DIR] [SESSIONS_DIR]
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


def is_censored(turn: dict) -> bool:
    # A recovered timeout (real turn.completed flushed after the kill, picked up
    # by the post-run reparse pass) is NOT censored — count it. Belt-and-braces:
    # the reparse also clears timed_out/score.censored, but honor the flag too so
    # the analyzer is correct even on a record where only the flag was set.
    if turn.get("recovered_after_timeout"):
        return False
    if turn.get("timed_out"):
        return True
    sc = turn.get("score") or {}
    return bool(sc.get("censored"))


# ---------- source A: harness saved per-run JSON (cost + censored) ----------
def load_runs(out_dir: Path):
    """arm -> run -> turn -> {'total': gross input, 'uncached': int, 'censored': bool}."""
    cost = defaultdict(lambda: defaultdict(dict))
    censored_count = 0
    for p in out_dir.glob("*_run*.json"):
        if p.name.startswith("endstate_"):
            continue
        m = re.match(r"^(.+)_run(\d+)\.json$", p.name)
        if not m:
            continue
        arm, run = m.group(1), int(m.group(2))
        try:
            turns = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for t in turns:
            ti = t.get("turn")
            if ti is None:
                continue
            u = t.get("usage") or {}
            cen = is_censored(t)
            if cen:
                censored_count += 1
            cost[arm][run][ti] = {
                "total": (u.get("input_tokens") or 0) if not cen else None,  # gross input = published total
                "uncached": (u.get("uncached_tokens") if u.get("uncached_tokens") is not None
                             else (u.get("input_tokens", 0) - u.get("cached_tokens", 0))) if not cen else None,
                "censored": cen,
            }
    return cost, censored_count


# ---------- source B: thread_id from raw stdout ----------
def thread_id_of(path: Path):
    try:
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
                    return o["thread_id"]
    except Exception:
        pass
    return None


def load_thread_ids(out_dir: Path):
    """arm -> run -> turn -> thread_id (from raw stdout)."""
    pat = re.compile(r"^run(\d+)_(.+)_t(\d+)\.jsonl$")
    tids = defaultdict(lambda: defaultdict(dict))
    for p in out_dir.glob("run*_t*.jsonl"):
        m = pat.match(p.name)
        if m:
            tids[m.group(2)][int(m.group(1))][int(m.group(3))] = thread_id_of(p)
    return tids


# ---------- source C: codex rollouts (occupancy + compaction) ----------
def index_rollouts(sessions_dir: Path):
    paths = []
    if sessions_dir.exists():
        for root, _d, files in os.walk(sessions_dir):
            for fn in files:
                if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                    paths.append(Path(root) / fn)
    return paths


def find_rollouts(tid, rollout_paths):
    """ALL rollout files for a thread, chronologically (a thread can roll over)."""
    if not tid:
        return []
    matches = [p for p in rollout_paths if tid in p.name]
    return sorted(matches, key=lambda p: p.name)


def rollout_rows(paths):
    """Concatenate (primary_type, occupancy, is_compact) rows + window across files."""
    rows, window = [], None
    for path in paths:
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
                occ = None
                for lt in find_key(o, "last_token_usage"):
                    if isinstance(lt, dict) and lt.get("input_tokens") is not None:
                        occ = max(occ or 0, lt["input_tokens"])
                for w in find_key(o, "model_context_window"):
                    if w:
                        window = w
                rows.append((pt, occ, bool(pt and "compact" in pt.lower())))
    return rows, window


def segment(rows):
    """Split rows into per-turn segments on the boundary event. NO implicit leading
    segment: pre-boundary rows are ignored so segments[t-1] aligns to turn t and the
    first-compaction turn is attributed correctly (review #4a)."""
    counts = defaultdict(int)
    for pt, _o, _c in rows:
        if pt:
            counts[pt] += 1
    boundary = next((b for b in BOUNDARY_TYPES if counts.get(b)), None)
    segs, cur = [], None
    for pt, occ, comp in rows:
        if boundary and pt == boundary:
            cur = {"occ": 0, "comp": 0}
            segs.append(cur)
        if cur is None:
            continue  # drop pre-boundary preamble
        if occ is not None:
            cur["occ"] = max(cur["occ"], occ)
        if comp:
            cur["comp"] += 1
    if boundary is None and rows:
        # no boundary markers -> treat whole rollout as a single turn
        s = {"occ": 0, "comp": 0}
        for _pt, occ, comp in rows:
            if occ is not None:
                s["occ"] = max(s["occ"], occ)
            if comp:
                s["comp"] += 1
        segs = [s]
    return segs


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

    cost, censored_count = load_runs(out_dir)
    if not cost:
        raise SystemExit("No {arm}_run{N}.json files in {0} (run the chain first)".format(out_dir))
    tids = load_thread_ids(out_dir)
    rollout_paths = index_rollouts(sessions_dir)

    lines = []
    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 72)
    out("COMPACTION-REGIME ANALYSIS")
    out("  out_dir:      {0}".format(out_dir))
    out("  sessions_dir: {0}  ({1} rollouts indexed)".format(sessions_dir, len(rollout_paths)))
    out("  window={0:,}  threshold(~90%)={1:,}  censored turns excluded={2}".format(
        WINDOW, COMPACTION_THRESHOLD, censored_count))
    out("  'total' = gross input_tokens (cache-inclusive) — the published metric (BENCHMARKS.md:62)")
    out("=" * 72)

    # occupancy + compaction per (arm, run, turn) from rollouts
    occ = defaultdict(lambda: defaultdict(dict))
    comp = defaultdict(lambda: defaultdict(dict))
    windows = set()
    misses = 0
    seg_mismatch = []   # (arm, run, n_segments, n_turns) — boundary assumption may be wrong
    for arm in cost:
        for run in cost[arm]:
            turn_tids = tids.get(arm, {}).get(run, {})
            uniq = set(v for v in turn_tids.values() if v)
            if len(uniq) == 1:  # resumed thread (builtin): one thread, segment into turns
                rps = find_rollouts(next(iter(uniq)), rollout_paths)
                if not rps:
                    misses += 1
                    for t in sorted(cost[arm][run]):
                        occ[arm][run][t] = None
                        comp[arm][run][t] = 0
                else:
                    rows, w = rollout_rows(rps)
                    if w:
                        windows.add(w)
                    segs = segment(rows)
                    # If #segments != #turns the per-turn boundary assumption (BOUNDARY_TYPES)
                    # is off — alignment of occupancy/compaction to turns (hence the in-regime
                    # window `f`) is then unreliable. Surface it instead of silently mis-aligning.
                    if len(segs) != len(cost[arm][run]):
                        seg_mismatch.append((arm, run, len(segs), len(cost[arm][run])))
                    for t in sorted(cost[arm][run]):
                        s = segs[t - 1] if 0 <= t - 1 < len(segs) else None
                        occ[arm][run][t] = s["occ"] if s else None
                        comp[arm][run][t] = s["comp"] if s else 0
            else:  # fresh thread per turn
                for t in sorted(cost[arm][run]):
                    rps = find_rollouts(turn_tids.get(t), rollout_paths)
                    if not rps:
                        misses += 1
                        occ[arm][run][t] = None
                        comp[arm][run][t] = 0
                        continue
                    rows, w = rollout_rows(rps)
                    if w:
                        windows.add(w)
                    segs = segment(rows)
                    occ[arm][run][t] = max((s["occ"] for s in segs), default=0)
                    comp[arm][run][t] = sum(s["comp"] for s in segs)

    out("windows logged in rollouts: {0}".format(sorted(windows) or ["<none matched>"]))
    if misses:
        out("WARNING: {0} thread(s) had no matching rollout — occupancy/compaction unavailable "
            "for those (cost metrics still valid). Check SESSIONS_DIR.".format(misses))
    if seg_mismatch:
        out("WARNING: rollout segment count != turn count for {0} — the per-turn boundary "
            "assumption (BOUNDARY_TYPES) is likely wrong, so occupancy/compaction turn-alignment "
            "(and the in-regime window) are UNRELIABLE. Inspect a rollout's turn markers before "
            "trusting the PRIMARY verdict.".format(
                ", ".join("{0}/run{1}({2}seg!={3}turn)".format(*m) for m in seg_mismatch)))
    # The reviewer's "eyeball builtin occupancy for monotonic growth" — automated:
    # a resumed transcript should grow turn-over-turn; a non-monotonic curve means the
    # segmentation mis-aligned turns (so the first-compaction turn `f` can't be trusted).
    nonmono = []
    for run in cost.get("builtin", {}):
        ts = sorted(occ["builtin"][run])
        # Exempt the legitimate compaction sawtooth: compaction by design drops per-call
        # occupancy ~70% (e.g. T10=222k -> T11=63k) — that is the SUCCESS signal, not a
        # segmentation mis-alignment. The event is logged on the peak turn (T10) while the
        # reset shows on the NEXT turn (T11), so exempt a drop when compaction fired at
        # EITHER endpoint of the pair. A drop with no adjacent compaction is still flagged.
        suspicious = False
        for ta, tb in zip(ts, ts[1:]):
            a, b = occ["builtin"][run].get(ta), occ["builtin"][run].get(tb)
            if a is None or b is None:
                continue
            adjacent_compaction = (comp["builtin"][run].get(ta, 0) > 0
                                   or comp["builtin"][run].get(tb, 0) > 0)
            if b < a * 0.9 and not adjacent_compaction:
                suspicious = True
                break
        if suspicious:
            nonmono.append(run)
    if nonmono:
        out("WARNING: builtin per-turn occupancy is non-monotonic in run(s) {0} — expected to "
            "grow as the resumed transcript accumulates. Segmentation alignment is suspect; "
            "verify before trusting the in-regime window.".format(nonmono))
    out("")

    builtin_first_compaction = {}
    for arm in sorted(cost):
        out("-" * 72)
        out("ARM: {0}".format(arm))
        for run in sorted(cost[arm]):
            turns = sorted(cost[arm][run])
            totals = [cost[arm][run][t]["total"] for t in turns]
            occs = [occ[arm][run].get(t) for t in turns]
            comps = [comp[arm][run].get(t, 0) for t in turns]
            cen = [t for t in turns if cost[arm][run][t]["censored"]]
            peak = max((o for o in occs if o is not None), default=None)
            firstc = next((t for t in turns if comp[arm][run].get(t, 0) > 0), None)
            crossed = next((t for t in turns if (occ[arm][run].get(t) or 0) >= COMPACTION_THRESHOLD), None)
            if arm == "builtin":
                builtin_first_compaction[run] = firstc
            out("  run{0}: peak_occ={1}  crossed~233k@T{2}  firstCompaction@T{3}  censored={4}".format(
                run, "{:,}".format(peak) if peak is not None else "?", crossed or "-", firstc or "-", cen or "-"))
            out("        total/turn (gross input): {0}".format(
                ["{:,}".format(x) if x is not None else "CENSORED" for x in totals]))
            out("        occ/turn:                 {0}".format(
                ["{:,}".format(o) if o is not None else "?" for o in occs]))
            out("        compaction/turn:          {0}".format(comps))
        out("")

    out("=" * 72)
    out("VALIDITY GATE")
    n_builtin = len(cost.get("builtin", {}))
    fired = [r for r, t in builtin_first_compaction.items() if t is not None]
    # Pre-registered gate is >=4/5 firing (docs/COMPACTION_REGIME_TEST.md §2.3/§4.3),
    # NOT all-fire. min(MIN_FIRING_RUNS, n_builtin) keeps small-N sane (N=2 needs 2/2).
    coverage_full = (n_builtin > 0 and len(fired) >= min(MIN_FIRING_RUNS, n_builtin))
    provisional = n_builtin < MIN_FIRING_RUNS  # below the N=5 design's run count
    out("  compaction fired in {0}/{1} builtin runs -> regime {2}".format(
        len(fired), n_builtin, "CONFIRMED" if coverage_full else "PARTIAL"))
    if provisional:
        out("  NOTE: N={0} < design N={1} -> result is PROVISIONAL: trust the DIRECTION, treat".format(
            n_builtin, MIN_FIRING_RUNS))
        out("        the magnitude as a small-sample point estimate (no robust CI). Run N>={0} to promote.".format(MIN_FIRING_RUNS))
    if not coverage_full:
        out("  >>> some builtin runs did NOT compact: extend/heavy-up the fixture (occ never ~233k),")
        out("      or suspect #16033 / a config override (occ crosses but no compaction event).")
    regime_valid = coverage_full
    out("")

    out("=" * 72)
    out("PRIMARY METRIC — marginal in-regime ratio (one ratio per run, CI over runs)")
    if "builtin" not in cost or "with_session" not in cost:
        out("  need BOTH builtin and with_session arms.")
    elif not fired:
        out("  no in-regime turns (no compaction) — cannot compute (see validity gate).")
    else:
        per_run = []                     # one marginal ratio per run (review #3)
        by_turn = defaultdict(list)
        for run, f in builtin_first_compaction.items():
            if f is None or run not in cost["with_session"]:
                continue
            b_sum = w_sum = 0
            for t in sorted(cost["builtin"][run]):
                if t < f:
                    continue
                b = cost["builtin"][run][t]["total"]
                wd = cost["with_session"][run].get(t)
                w = wd["total"] if wd else None
                if b is None or w in (None, 0):  # skip censored / missing (review #2)
                    continue
                b_sum += b
                w_sum += w
                by_turn[t].append(b / w)
            if w_sum > 0:
                per_run.append(b_sum / w_sum)
        if per_run:
            mean, half = statistics.mean(per_run), ci95(per_run)
            lo, hi = mean - half, mean + half
            out("  per-run marginal ratios: {0}  (n={1})".format(
                ["{0:.2f}x".format(r) for r in per_run], len(per_run)))
            out("  marginal ratio = {0:.2f}x   95% CI ~ [{1:.2f}, {2:.2f}]".format(mean, lo, hi))
            out("  per-turn-index (descriptive): " + ", ".join(
                "T{0}={1:.2f}x".format(t, statistics.mean(by_turn[t])) for t in sorted(by_turn)))
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
            out("  no usable in-regime turn pairs (all censored/missing?).")
    out("")

    out("=" * 72)
    out("SECONDARY — cumulative total ratio (censored excluded; contaminated by sub-threshold turns, §C2):")
    def total(arm):
        return sum(cost[arm][r][t]["total"] for r in cost.get(arm, {})
                   for t in cost[arm][r] if cost[arm][r][t]["total"] is not None)
    if "builtin" in cost and "with_session" in cost:
        bu, wu = total("builtin"), total("with_session")
        # MATCHED: only (run,turn) where BOTH arms non-censored. Lead with this — the
        # unmatched ratio is inflated by asymmetric censoring (one arm drops more turns).
        bm = wm = 0
        for r in cost["builtin"]:
            if r not in cost["with_session"]:
                continue
            for t in cost["builtin"][r]:
                bt = cost["builtin"][r][t]["total"]
                wd = cost["with_session"][r].get(t)
                wt = wd["total"] if wd else None
                if bt is not None and wt is not None:
                    bm += bt
                    wm += wt
        out("  MATCHED (fair): builtin={0:,}  with_session={1:,}  ratio={2:.2f}x".format(
            bm, wm, (bm / wm) if wm else 0))
        out("  unmatched (inflated by asymmetric censoring — do NOT headline): "
            "builtin={0:,}  with_session={1:,}  ratio={2:.2f}x".format(bu, wu, (bu / wu) if wu else 0))
    out("")
    out("Lead with TOTAL (gross input) tokens. Do NOT publish an uncached number from this run:")
    out("arms are block-sequential (not interleaved) and compaction busts the prefix cache at the")
    out("boundary, so uncached is warmth-confounded here (§4.2/§4.5).")

    report = out_dir / "COMPACTION_ANALYSIS.md"
    report.write_text("```\n" + "\n".join(lines) + "\n```\n", encoding="utf-8")
    print("\n[written] {0}".format(report))


if __name__ == "__main__":
    main()
