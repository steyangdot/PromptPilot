"""Re-test analysis: uncached input + cache-aware cost-per-success per arm.

The 2026-06-07 compaction pilot showed the per-turn GROSS `input_tokens` is
~95% cached re-reads of the transcript, so it overstates native resume's real
cost. This script recomputes the honest quantities from each arm's saved per-run
files (`{arm}_run{N}.json`) — works on old and new runs because it reads raw
`usage` (input_tokens / cached_tokens / total_cost_usd), not the aggregate schema.

Per arm it reports, summed over a chain's turns and averaged over runs:
  - sum success            (the quality signal)
  - GROSS input            (≈95% cached — contrast only, NOT the headline)
  - UNCACHED input         (real incremental context the model paid full price for)
  - cache-aware $          (claude = self-reported total_cost_usd, real & model-accurate;
                            codex = NOTIONAL o4-mini proxy — gpt-5.5 rates unavailable)
  - $ / success            (cost efficiency; '   n/a' when an arm scored 0 success)

Usage:
  python research/analyze_uncached_cost.py [RUN_DIR] [--chain chain1] [--tool all]
  RUN_DIR defaults to PROMPTPILOT_OUT_DIR or research/data/chain_results_v2.
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
from agentic_variety_test import (  # noqa: E402
    claude_cost, codex_cost, turn_timed_out, _turn_uncached,
)
from score_endstate import _score_run, endstate_score  # noqa: E402

ARMS = ["no_session", "with_session", "slm_native", "stacked", "builtin", "gated_session"]
ARM_LABEL = {
    "no_session": "NO_SESSION (rewrite, no session)",
    "with_session": "WITH_SESSION (rewrite + bounded)",
    "slm_native": "slm_native (rewrite + native)",
    "stacked": "stacked (rewrite+bounded+native)",
    "builtin": "BUILTIN (raw + native)",
    "gated_session": "GATED (rewrite + gated bounded)",
}


def _turn_cost(usage: dict, tool: str) -> float:
    return claude_cost(usage) if tool == "claude-code" else codex_cost(usage)


def _arm_stats(arm_dir: Path, arm: str, tool: str) -> dict | None:
    """Aggregate one arm across its run files. Returns per-chain sums meaned over runs."""
    run_files = sorted(glob.glob(str(arm_dir / f"{arm}_run*.json")))
    if not run_files:
        return None
    # Timed-out turns recorded 0 tokens/$0 while still earning file-hash success
    # (claude '{}' overwrite; codex parse-before-orphan-flush). Counting them
    # inflated the headline ratios. EXCLUDE-and-COUNT them here. Recovery
    # asymmetry: codex timeouts are recoverable (a late turn.completed carries
    # real usage) while claude's are lost — so a timed-out turn that still has
    # non-zero recorded uncached (a recovered/grace-wait codex turn) is routed to
    # a SEPARATE `recovered` bucket, never silently folded into the clean mean.
    per_run = {"success": [], "gross": [], "uncached": [], "cached": [], "cost": [], "calls": []}
    timed_out_per_run, recovered_cnt_per_run, recovered_unc_per_run = [], [], []
    models: set[str] = set()
    for rf in run_files:
        try:
            turns = json.loads(Path(rf).read_text(encoding="utf-8"))
        except Exception:
            continue
        s = g = unc = cac = cost = calls = 0.0
        n_timed_out = n_recovered = rec_unc = 0
        for t in turns:
            mr = t.get("model_used") or t.get("model_resolved")
            if mr:
                models.add(mr)
            if turn_timed_out(t, tool):
                turn_unc = _turn_uncached(t)
                if turn_unc > 0:          # recovered (real usage despite timeout)
                    n_recovered += 1
                    rec_unc += turn_unc
                else:                     # censored & lost — exclude, count
                    n_timed_out += 1
                continue                  # never folded into clean aggregates
            u = t["usage"]
            inp = u.get("input_tokens", 0)
            cached = u.get("cached_tokens", 0)
            s += t["score"]["success"]
            g += inp
            unc += inp - cached
            cac += cached
            cost += _turn_cost(u, tool) + t.get("slm_cost", 0.0)
            calls += u.get("tool_calls", 0)
        for k, v in (("success", s), ("gross", g), ("uncached", unc),
                     ("cached", cac), ("cost", cost), ("calls", calls)):
            per_run[k].append(v)
        timed_out_per_run.append(n_timed_out)
        recovered_cnt_per_run.append(n_recovered)
        recovered_unc_per_run.append(rec_unc)
    if not per_run["success"]:
        return None
    n = len(per_run["success"])
    out = {k: statistics.mean(v) for k, v in per_run.items()}
    out["n_runs"] = n
    out["timed_out"] = sum(timed_out_per_run)
    out["recovered"] = sum(recovered_cnt_per_run)
    out["recovered_uncached"] = statistics.mean(recovered_unc_per_run)
    out["models"] = sorted(models)

    # End-state (goal-completion) score per run — the churn-blind denominator that
    # the per-turn `success` sum is NOT (the per-turn sum rewards arms that keep
    # editing). Best-effort: needs a captured endstate_*.json (any tool) or a
    # mineable codex transcript; arms without either report endstate=None.
    es_scores = []
    for rf in run_files:
        m = re.search(r"_run(\d+)\.json$", os.path.basename(rf))
        if not m:
            continue
        try:
            rec, _src = _score_run(arm_dir, arm, int(m.group(1)))
            sc, _conf = endstate_score(rec)
            if sc is not None:
                es_scores.append(sc)
        except Exception:
            continue
    out["endstate"] = statistics.mean(es_scores) if es_scores else None
    out["endstate_n"] = len(es_scores)
    return out


def _check_cross_arm_models(rows: list) -> None:
    """Warn if arms were run on different models — cross-arm uncached/$ ratios
    confound the session mechanism with model drift (the audit's unguarded hole;
    the only prior guard lived in the chain4/chain5 analyzers and checked within
    one arm). Warns, never aborts (legacy files legitimately lack provenance)."""
    seen: dict[str, set] = {}
    for arm, st in rows:
        for m in st.get("models", []):
            seen.setdefault(m, set()).add(arm)
    if len(seen) > 1:
        print("  !! MODEL DRIFT across arms — cross-arm ratios confound the session")
        print("     mechanism with a model-snapshot delta:")
        for m, arms in sorted(seen.items()):
            print(f"       {m}: {', '.join(sorted(arms))}")
    elif seen and set(seen) <= {"codex-default", "None"}:
        print("  note: codex model provenance absent ('codex-default') — model "
              "equality across arms cannot be verified (set CODEX_MODEL to pin it).")


def analyze_chain(run_dir: Path, tool: str, chain_id: str) -> None:
    cdir = run_dir / tool / chain_id
    if not cdir.is_dir():
        return
    rows = []
    for arm in ARMS:
        st = _arm_stats(cdir, arm, tool)
        if st:
            rows.append((arm, st))
    if not rows:
        print(f"  [skip] no arm run-files in {cdir}")
        return

    print()
    print("=" * 100)
    notional = " (codex $ = NOTIONAL o4-mini proxy)" if tool == "codex" else ""
    print(f"  {tool}  /  {chain_id}{notional}")
    print("  GROSS input is ~95% cached re-reads -- shown for contrast only; UNCACHED is the headline.")
    print("  succ/UNCACHED/$ EXCLUDE timed-out (censored) turns; 'TO'=excluded, 'rec'=recovered.")
    print("  $/succ = $ per per-turn-success-SUM (churn-sensitive); $/es = $ per END-STATE")
    print("  goal-completion score (churn-blind, the trustworthy denominator).")
    print("=" * 100)
    hdr = "  {:<32} {:>4} {:>6} {:>11} {:>11} {:>8} {:>9} {:>3} {:>3} {:>5} {:>9}".format(
        "arm", "runs", "succ", "gross_in", "UNCACHED", "$", "$/succ", "TO", "rec", "es", "$/es")
    print(hdr)
    print("  " + "-" * 96)
    for arm, st in rows:
        cps = ("{:>9.4f}".format(st["cost"] / st["success"])
               if st["success"] > 0 else "      n/a")
        es = st.get("endstate")
        es_s = "{:.2f}".format(es) if es is not None else " n/a"
        cps_es = ("{:>9.4f}".format(st["cost"] / es) if es else "      n/a")
        print("  {:<32} {:>4d} {:>6.2f} {:>11,.0f} {:>11,.0f} {:>8.4f} {} {:>3d} {:>3d} {:>5} {}".format(
            ARM_LABEL.get(arm, arm), st["n_runs"], st["success"],
            st["gross"], st["uncached"], st["cost"], cps,
            st.get("timed_out", 0), st.get("recovered", 0), es_s, cps_es))
    print("  " + "-" * 96)
    _check_cross_arm_models(rows)

    # Pairwise headline ratios when both arms present
    by = {arm: st for arm, st in rows}
    def ratio(a, b, key):
        if a in by and b in by and by[b][key]:
            return by[a][key] / by[b][key]
        return None
    pairs = [
        ("WITH vs slm_native (bounded vs native, rewrite held constant)", "with_session", "slm_native"),
        ("WITH vs BUILTIN (full product vs raw+native)", "with_session", "builtin"),
        ("slm_native vs BUILTIN (rewrite effect, native held constant)", "slm_native", "builtin"),
    ]
    printed_hdr = False
    for label, a, b in pairs:
        if a in by and b in by:
            if not printed_hdr:
                print("  Headline ratios (lower input/$ = better):")
                printed_hdr = True
            ru = ratio(b, a, "uncached")   # how many x more uncached the native/raw arm uses
            rc = ratio(b, a, "cost")
            ru_s = f"{ru:.2f}x uncached" if ru else "n/a uncached"
            rc_s = f"{rc:.2f}x $" if rc else "n/a $"
            print(f"    {label}:")
            print(f"        {b} uses {ru_s}, {rc_s} vs {a}")
            # Censored-turn warning: timed-out turns recorded 0 tokens, so the
            # UNCACHED ratio is NOT corrected by excluding them — the more-censored
            # arm's uncached is deflated (a LOWER bound). Recovery (codex) or
            # imputation (claude) is required for the true ratio.
            to_a, to_b = by[a].get("timed_out", 0), by[b].get("timed_out", 0)
            if to_a or to_b:
                worse = b if to_b >= to_a else a
                print(f"        !! uncached ratio UNRELIABLE: {to_b} censored turn(s) in "
                      f"{b}, {to_a} in {a} (recorded 0 tokens). The more-censored arm "
                      f"({worse}) is deflated -- this is a lower bound. Run "
                      f"recover_uncached.py for the recovered/imputed figure.")
    print()


def main() -> None:
    default_dir = os.environ.get(
        "PROMPTPILOT_OUT_DIR",
        str(Path(__file__).parent / "data" / "chain_results_v2"))
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", nargs="?", default=default_dir)
    ap.add_argument("--chain", default="all")
    ap.add_argument("--tool", default="all", choices=["codex", "claude-code", "all"])
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    tools = ["codex", "claude-code"] if args.tool == "all" else [args.tool]
    for tool in tools:
        tdir = run_dir / tool
        if not tdir.is_dir():
            continue
        chains = ([args.chain] if args.chain != "all"
                  else sorted(p.name for p in tdir.iterdir() if p.is_dir()))
        for chain_id in chains:
            analyze_chain(run_dir, tool, chain_id)


if __name__ == "__main__":
    main()
