"""STAGE 0 — zero-token offline mining of the F1 chain_long artifacts (with_memory v2 spec L(-1)).

Decides, from data already on disk, whether the Stage-C epoch track is worth building at all,
and calibrates the Stage-B/C parameters. Analyses (docs/SESSION_MEMORY_V2_PANEL_SPEC.json):
  (a) EpochPolicy replay over the with_memory per-turn logs -> simulated epoch schedule.
      BUILD GATE: mean epoch length >= 3 warm turns AND warm fraction >= 40%.
  (b) Cold-uncached decomposition: pack-displaceable re-exploration vs residual warmth-addressable.
  (c) Warm total-vs-ctx curve + max single-turn thread growth (builtin runs, delta-corrected)
      -> calibrated epoch ceiling.
  (d) Kill-before-build economics: residual warm saving x simulated warm turns vs Stage-C spend.
  (e) Capability-elastic retro-baselines: SLM intent/scope accuracy, guard recall/false-fire.

APPROXIMATIONS (labelled, kill-only use — this pass can cancel Stage C, never substitute its probes):
  * warm-entry file-overlap uses fixture expected_files (spec.target_files was not logged in F1).
  * guard-fire detection = "PREFER ADDITIVE" in the logged memory_prefix (the guard directive);
    a non-empty prefix alone is only the always-on state summary.
  * simulated warm ctx growth per turn reuses the COLD turn's (uncached_input + output) as the
    content that turn would have added to a warm thread.
  * pack-displaceable = first-feed output of read-like commands touching a path already seen in
    a PRIOR turn of the same run (path regex over command text + file_change items).

Run:  python research/stage0_mine_f1.py
"""
import json
import os
import re
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain_test_v2 import delta_cumulative_usage  # noqa: E402  (cumulative-corrected parsing)
from prpt.memory import REFACTOR_KW, _mentions     # noqa: E402  (the shipped guard's own rules)

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                 "chain_results_v2", "codex", "chain_long")
DESTRUCTIVE_KW = ("remove", "delete", "drop", "deprecate", "retire", "strip", "prune", "clean up")
CEILING_EST = 120_000     # pre-calibration placeholder; (c) recomputes
K_MAX = 4                 # micro-epoch max warm turns (spec default)
PREAMBLE = 12_000         # codex preamble+instructions estimate for the ctx gauge
_PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|toml|cfg|md|txt|ini)")


def load(arm, r):
    p = os.path.join(D, "{0}_run{1}.json".format(arm, r))
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


# --------------------------------------------------------------------------- (a)
def replay_epoch_policy(recs):
    """Simulate the hardened risk-gated policy over one with_memory run's 13 turns.
    Returns per-turn modes + epoch stats. Mode 'seed' = fresh exec opening an epoch;
    'warm' = resumed turn; 'cold' = risk/eligibility-forced fresh exec outside an epoch."""
    modes, epochs = [], []
    cur_warm, est_ctx, epoch_changed = 0, 0, set()
    in_epoch = False
    for i, rec in enumerate(recs):
        raw = (rec.get("raw") or "").lower()
        mp = rec.get("memory_prefix") or ""
        guard_fired = "PREFER ADDITIVE" in mp
        is_refactor = any(_mentions(k, raw) for k in REFACTOR_KW) or rec.get("scope") == "broad"
        destructive = any(k in raw for k in DESTRUCTIVE_KW)
        prev = recs[i - 1] if i else None
        pv = (prev or {}).get("verify") or {}
        prev_green = bool(pv.get("ran")) and bool(pv.get("passed")) and not pv.get("retries")
        prev_ok = prev is not None and not prev.get("timed_out") and prev_green
        overlap = bool(set(rec.get("expected_files") or [])
                       & set((prev or {}).get("score", {}).get("changed") or []))
        eligible = (rec.get("intent") == "act" and rec.get("scope") == "localized"
                    and rec.get("expected_action") == "modify"
                    and prev_ok and overlap and rec.get("ledger_ok", True))
        risky = is_refactor or guard_fired or destructive
        grow = (rec.get("uncached_input") or 0) + (rec.get("usage", {}).get("output_tokens") or 0)
        if risky or not eligible:
            if in_epoch and cur_warm:
                epochs.append(cur_warm)
            in_epoch, cur_warm, est_ctx, epoch_changed = False, 0, 0, set()
            modes.append("cold")
            continue
        if not in_epoch:                       # low-risk + eligible -> open an epoch
            in_epoch, cur_warm = True, 0
            est_ctx = PREAMBLE + grow
            modes.append("seed")
        elif cur_warm >= K_MAX or est_ctx + grow > CEILING_EST:
            epochs.append(cur_warm)            # cut on budget; this turn re-seeds
            cur_warm, est_ctx = 0, PREAMBLE + grow
            modes.append("seed")
        else:
            cur_warm += 1
            est_ctx += grow
            modes.append("warm")
        epoch_changed |= set(rec.get("score", {}).get("changed") or [])
    if in_epoch and cur_warm:
        epochs.append(cur_warm)
    warm = modes.count("warm")
    return dict(modes=modes, epochs=epochs, warm_turns=warm,
                warm_fraction=warm / len(recs),
                mean_epoch_len=(st.mean(epochs) if epochs else 0.0))


# --------------------------------------------------------------------------- (b)
def _norm(p):
    return p.replace("\\", "/").lstrip("./").lower()


def _match_any(paths, cands):
    """Suffix-tolerant path match (command paths may be absolute/relative variants)."""
    for a in paths:
        na = _norm(a)
        for c in cands:
            if na.endswith(c) or c.endswith(na):
                return True
    return False


def decompose_turn_streams(run_idx, recs):
    """Per-turn first-feed tool-output tokens + TWO re-exploration measures:
      displaceable_any  = outputs of commands touching ANY previously-seen path (upper bound);
      displaceable_pack = the subset whose prior-seen paths the PACK CANDIDATE RULE would have
                          injected that turn (targets ∪ guard-surfaced files ∪ prev-turn modified)
                          — the pack's true addressable market (external review P1-1).
    Also returns gross input per turn for the amplification factor m."""
    seen_paths, rows = set(), []
    prev_changed = set()
    for t in range(1, 14):
        p = os.path.join(D, "run{0}_with_memory_t{1}.jsonl".format(run_idx, t))
        rec = recs[t - 1] if t - 1 < len(recs) else {}
        if not os.path.exists(p):
            rows.append(None); continue
        # the pack candidate set the ALGORITHM would build for this turn (labeled proxies:
        # expected_files for spec.target_files; guard-surfaced files parsed from memory_prefix)
        pack_cands = {_norm(f) for f in (rec.get("expected_files") or [])}
        pack_cands |= {_norm(f) for f in _PATH_RE.findall(rec.get("memory_prefix") or "")}
        pack_cands |= {_norm(f) for f in prev_changed}
        total_out = redo_any = redo_pack = 0
        turn_paths = set()
        for line in open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") != "item.completed":
                continue
            it = ev.get("item") or {}
            if it.get("type") == "command_execution":
                out_toks = len(it.get("aggregated_output") or "") // 4
                total_out += out_toks
                paths = {_norm(x) for x in _PATH_RE.findall(it.get("command") or "")}
                turn_paths |= paths
                prior = paths & seen_paths
                if prior:                        # touches a file some EARLIER turn already saw
                    redo_any += out_toks
                    if _match_any(prior, pack_cands):
                        redo_pack += out_toks
            elif it.get("type") == "file_change":
                turn_paths |= {_norm(x) for x in _PATH_RE.findall(json.dumps(it))}
        rows.append(dict(first_feed=total_out, displaceable=redo_any, displaceable_pack=redo_pack,
                         gross_in=(rec.get("usage", {}).get("input_tokens") or 0)))
        seen_paths |= turn_paths
        prev_changed = set(rec.get("score", {}).get("changed") or [])
    return rows


# --------------------------------------------------------------------------- (c)
def warm_curve(recs):
    """Builtin run: per-turn marginal totals vs estimated thread size (delta-corrected)."""
    prev_raw, pts = None, []
    ctx = PREAMBLE
    for rec in recs:
        raw_u = rec.get("usage_cumulative_raw") or rec.get("usage") or {}
        usage, sem = delta_cumulative_usage(raw_u, prev_raw)
        if (raw_u.get("input_tokens") or 0) > 0:
            prev_raw = raw_u
        d_unc = usage.get("uncached_tokens") or 0
        d_out = usage.get("output_tokens") or 0
        d_tot = (usage.get("input_tokens") or 0) + d_out
        pts.append(dict(turn=rec.get("turn"), ctx=ctx, d_total=d_tot, d_unc=d_unc, growth=d_unc + d_out))
        ctx += d_unc + d_out
    return pts


# --------------------------------------------------------------------------- (e)
def retro_baselines(runs):
    n_act = n_act_ok = n_exp = n_exp_ok = 0
    guard_recall_hits = guard_false = destructive_turns = nonref_turns = 0
    for recs in runs:
        for rec in recs:
            raw = (rec.get("raw") or "").lower()
            mp = rec.get("memory_prefix") or ""
            fired = "PREFER ADDITIVE" in mp
            is_refactor = any(_mentions(k, raw) for k in REFACTOR_KW)
            if rec.get("expected_action") == "modify":
                n_act += 1; n_act_ok += (rec.get("intent") == "act")
            else:
                n_exp += 1; n_exp_ok += (rec.get("intent") in ("answer", "explain"))
            if is_refactor:
                destructive_turns += 1; guard_recall_hits += fired
            else:
                nonref_turns += 1; guard_false += fired
    return dict(intent_act_acc=n_act_ok / max(1, n_act), intent_exp_acc=n_exp_ok / max(1, n_exp),
                guard_recall=guard_recall_hits / max(1, destructive_turns),
                guard_false_rate=guard_false / max(1, nonref_turns),
                refactor_turns=destructive_turns)


def main():
    wm = [load("with_memory", r) for r in (1, 2, 3)]
    bi = [load("builtin", r) for r in (1, 2, 3)]
    wm = [x for x in wm if x]; bi = [x for x in bi if x]

    print("=" * 96)
    print("(a) EPOCH-POLICY REPLAY (hardened risk gate; K_MAX={0}, ceiling~{1:,})".format(K_MAX, CEILING_EST))
    fracs, lens, all_epochs = [], [], []
    for i, recs in enumerate(wm, 1):
        r = replay_epoch_policy(recs)
        fracs.append(r["warm_fraction"]); all_epochs += r["epochs"]
        if r["epochs"]:
            lens.append(r["mean_epoch_len"])
        print("  run{0}: modes={1}".format(i, " ".join(m[0].upper() for m in r["modes"])))
        print("         epochs(warm-lens)={0}  warm_turns={1}/13  warm_fraction={2:.0%}".format(
            r["epochs"], r["warm_turns"], r["warm_fraction"]))
    mean_len = st.mean(all_epochs) if all_epochs else 0.0
    mean_frac = st.mean(fracs) if fracs else 0.0
    gate = mean_len >= 3 and mean_frac >= 0.40
    print("  BUILD GATE: mean epoch len {0:.2f} (need >=3)  warm fraction {1:.0%} (need >=40%)  -> {2}".format(
        mean_len, mean_frac, "PASS" if gate else "FAIL -> Stage C cancelled per spec"))

    print("=" * 96)
    print("(b) COLD-UNCACHED DECOMPOSITION (any-prior re-exploration vs PACK-SELECTED market)")
    disp_share_run, pack_share_run, m_run, pack_tok_run = [], [], [], []
    for i, recs in enumerate(wm, 1):
        rows = decompose_turn_streams(i, recs)
        ff = sum(r["first_feed"] for r in rows if r)
        dp = sum(r["displaceable"] for r in rows if r)
        dpk = sum(r["displaceable_pack"] for r in rows if r)
        gin = sum(r["gross_in"] for r in rows if r)
        unc = sum((rec.get("uncached_input") or 0) for rec in recs)
        disp_share_run.append(dp / max(1, unc))
        pack_share_run.append(dpk / max(1, unc))
        pack_tok_run.append(dpk / 13)
        m_run.append(gin / max(1, ff))
        print("  run{0}: first-feed ~{1:>8,} | any-prior ~{2:>8,} ({3:.0%} of unc) | "
              "PACK-SELECTED ~{4:>8,} ({5:.0%} of unc, ~{6:,.0f}/turn) | amplification m~{7:.1f}".format(
                  i, ff, dp, dp / max(1, unc), dpk, dpk / max(1, unc), dpk / 13, gin / max(1, ff)))
    disp_share = st.mean(disp_share_run)
    pack_share = st.mean(pack_share_run)
    pack_tok_turn = st.mean(pack_tok_run)
    m_amp = st.mean(m_run)
    print("  MEANS: any-prior {0:.0%} | pack-selected {1:.0%} (~{2:,.0f} tok/turn) | m~{3:.1f}".format(
        disp_share, pack_share, pack_tok_turn, m_amp))

    print("=" * 96)
    print("(c) WARM TOTAL-vs-CTX CURVE (builtin, delta-corrected)")
    growths, unc_means = [], []
    for i, recs in enumerate(bi, 1):
        pts = warm_curve(recs)
        growths += [p["growth"] for p in pts]
        unc_means.append(st.mean(p["d_unc"] for p in pts))
        line = "  run{0}: ".format(i) + "  ".join(
            "T{t}:ctx~{c}k d_tot={d}k".format(t=p["turn"], c=p["ctx"] // 1000, d=p["d_total"] // 1000)
            for p in pts[:6])
        print(line + "  ...")
    max_growth = max(growths) if growths else 0
    warm_marg_unc = st.mean(unc_means) if unc_means else 0
    ceiling = 233_000 - max_growth - 20_000
    print("  max single-turn thread growth ~{0:,} tok -> calibrated ceiling ~{1:,} (cliff 233k - growth - 20k)".format(
        max_growth, ceiling))
    print("  warm marginal uncached/turn (mean of run means) ~{0:,.0f} tok".format(warm_marg_unc))

    print("=" * 96)
    print("(d) KILL-BEFORE-BUILD ECONOMICS (uses the PACK-SELECTED share — conservative for the kill:")
    print("    a smaller pack slice leaves MORE for warmth, so if the kill holds here it is robust)")
    cold_unc_turn = st.mean(sum((rec.get("uncached_input") or 0) for rec in recs) / len(recs) for recs in wm)
    displaceable_turn = cold_unc_turn * pack_share
    residual = cold_unc_turn - displaceable_turn - warm_marg_unc
    sim_warm_per_run = st.mean(fracs) * 13 if fracs else 0
    saving_per_run = max(0, residual) * sim_warm_per_run
    print("  cold uncached/turn ~{0:,.0f} | pack-displaceable ~{1:,.0f} | warm marginal ~{2:,.0f}".format(
        cold_unc_turn, displaceable_turn, warm_marg_unc))
    print("  residual warmth-addressable/turn = {0:,.0f} tok".format(residual))
    print("  x simulated warm turns/run ({0:.1f}) = projected saving ~{1:,.0f} uncached tok/run".format(
        sim_warm_per_run, saving_per_run))
    print("  Stage-C spend >= probes + N=5x2 A/B (~100-150M total-token class from F1 run sizes)")
    print("  ECONOMICS VERDICT: {0}".format(
        "residual <= 0 -> warmth has NOTHING left after the pack -> KILL Stage C" if residual <= 0 else
        "positive residual -> Stage C remains conditional on its probes"))

    print("=" * 96)
    print("(e) CAPABILITY-ELASTIC RETRO-BASELINES (SLM decision quality in F1)")
    b = retro_baselines(wm)
    print("  intent acc on modify-turns: {0:.0%} | on explain-turns: {1:.0%}".format(
        b["intent_act_acc"], b["intent_exp_acc"]))
    print("  guard recall on refactor turns: {0:.0%} ({1} turns) | guard false-fire on non-refactor: {2:.0%}".format(
        b["guard_recall"], b["refactor_turns"], b["guard_false_rate"]))
    # usable-test coverage baseline (external review P2-4): of the turns where the guard fired,
    # how many surfaced at least one tests/ path (a runnable anchor for guard-test targeting)?
    fired = anchored = 0
    for recs in wm:
        for rec in recs:
            mp = rec.get("memory_prefix") or ""
            if "PREFER ADDITIVE" in mp:
                fired += 1
                anchored += ("tests/" in mp.replace("\\", "/"))
    print("  guard-fire turns with >=1 tests/ anchor surfaced: {0}/{1} ({2:.0%})".format(
        anchored, fired, anchored / max(1, fired)))

    print("=" * 96)
    print("STAGE-0 SUMMARY: epoch gate {0}; any-prior share ~{1:.0%}; PACK-SELECTED share ~{2:.0%} "
          "(~{3:,.0f} tok/turn, m~{4:.1f}); residual/turn {5:,.0f} tok; ceiling ~{6:,}".format(
              "PASS" if gate else "FAIL", disp_share, pack_share, pack_tok_turn, m_amp, residual, ceiling))


if __name__ == "__main__":
    main()
