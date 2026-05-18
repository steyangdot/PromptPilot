"""N=10 chain4 gated_session vs with_session analysis.

Computes mean success, token usage, cost, $/success, and gate-skip rate
for the Item #3 Opus 4.7 chain4 head-to-head.

    python analyze_chain4_gating.py
    python analyze_chain4_gating.py --runs 1-10
    python analyze_chain4_gating.py --gated-runs 1-10 --with-runs 1-10

Per-turn breakdowns included so we can spot whether savings concentrate
on the gated turns (T1, T2, T4 by chain4 design).
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

CHAIN_DIR = Path(__file__).parent / "chain_results_v2_opus" / "claude-code" / "chain4"


def parse_range(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def load_runs(chain_dir: Path, variant: str, indices: list[int]) -> list[list[dict]]:
    runs: list[list[dict]] = []
    for i in indices:
        p = chain_dir / f"{variant}_run{i}.json"
        if not p.exists():
            print(f"  WARNING: missing {p.name}")
            continue
        runs.append(json.loads(p.read_text(encoding="utf-8")))
    return runs


def per_turn(runs: list[list[dict]], key) -> list[list[float]]:
    n_turns = len(runs[0])
    return [[key(r[t]) for r in runs] for t in range(n_turns)]


def fmt(xs: list[float], nd: int = 3) -> list[float]:
    return [round(x, nd) for x in xs]


def _check_model_consistency(label: str, runs: list[list[dict]]) -> None:
    """Refuse to compute stats if runs were on different models.

    The fields model_resolved + model_used were added in fix-plan P1 #3;
    older runs lack them — those are silently skipped. New runs all get
    flagged consistently.
    """
    seen: set[str] = set()
    for r in runs:
        for t in r:
            m = t.get("model_used")
            if m:
                seen.add(m)
    if len(seen) > 1:
        raise SystemExit(
            f"[{label}] aborting: runs span multiple models {sorted(seen)} — "
            "comparison invalid. Re-run the inconsistent ones with a uniform "
            "CLAUDE_MODEL setting."
        )


def summarize(label: str, runs: list[list[dict]]) -> dict:
    n = len(runs)
    n_turns = len(runs[0]) if runs else 0
    if n == 0:
        print(f"[{label}] no runs"); return {}

    _check_model_consistency(label, runs)

    per_turn_succ = per_turn(runs, lambda t: t["score"]["success"])
    per_turn_intok = per_turn(runs, lambda t: t["usage"]["input_tokens"])
    per_turn_uncached = per_turn(runs, lambda t: t["usage"]["input_tokens"] - t["usage"].get("cached_tokens", 0))
    per_turn_outtok = per_turn(runs, lambda t: t["usage"]["output_tokens"])
    per_turn_cost = per_turn(runs, lambda t: t["usage"]["total_cost_usd"] + t.get("slm_cost", 0))
    per_turn_skip = per_turn(runs, lambda t: 1.0 if t.get("gate_skipped") else 0.0)

    run_means_succ = [statistics.mean([t["score"]["success"] for t in r]) for r in runs]
    run_total_intok = [sum(t["usage"]["input_tokens"] for t in r) for r in runs]
    run_total_uncached = [sum(t["usage"]["input_tokens"] - t["usage"].get("cached_tokens", 0) for t in r) for r in runs]
    run_total_cost = [sum(t["usage"]["total_cost_usd"] + t.get("slm_cost", 0) for t in r) for r in runs]
    run_skip_count = [sum(1 for t in r if t.get("gate_skipped")) for r in runs]

    overall_succ = statistics.mean(run_means_succ)
    sigma_succ = statistics.stdev(run_means_succ) if n >= 2 else 0.0
    mean_intok_per_turn = statistics.mean([statistics.mean(vs) for vs in per_turn_intok])
    mean_uncached_per_turn = statistics.mean([statistics.mean(vs) for vs in per_turn_uncached])
    mean_outtok_per_turn = statistics.mean([statistics.mean(vs) for vs in per_turn_outtok])
    mean_cost_per_run = statistics.mean(run_total_cost)
    total_skips = sum(run_skip_count)
    total_turns = n * n_turns

    print(f"[{label}] N={n} runs x {n_turns} turns")
    print(f"  per-run success means: {fmt(run_means_succ)}")
    print(f"  per-turn success means: {fmt([statistics.mean(vs) for vs in per_turn_succ])}")
    print(f"  per-turn input-tok means: {fmt([statistics.mean(vs) for vs in per_turn_intok], 0)}")
    print(f"  per-turn uncached-tok means: {fmt([statistics.mean(vs) for vs in per_turn_uncached], 0)}")
    print(f"  per-turn output-tok means: {fmt([statistics.mean(vs) for vs in per_turn_outtok], 0)}")
    print(f"  overall success mean: {overall_succ:.3f}  sigma={sigma_succ:.3f}")
    print(f"  mean input tok/turn (incl cached): {mean_intok_per_turn:,.0f}")
    print(f"  mean uncached tok/turn (billed):   {mean_uncached_per_turn:,.0f}")
    print(f"  mean output tok/turn:              {mean_outtok_per_turn:,.0f}")
    print(f"  mean $/run:  ${mean_cost_per_run:.3f}")
    if overall_succ > 0:
        dollar_per_success = mean_cost_per_run / (overall_succ * n_turns)
        print(f"  $/success:   ${dollar_per_success:.3f}  (cost per successful turn)")
    if total_turns > 0:
        print(f"  gate skips:  {total_skips}/{total_turns} ({100*total_skips/total_turns:.0f}%)")
    return {
        "n": n,
        "mean_succ": overall_succ,
        "sigma_succ": sigma_succ,
        "intok_per_turn": mean_intok_per_turn,
        "uncached_per_turn": mean_uncached_per_turn,
        "cost_per_run": mean_cost_per_run,
        "skips": total_skips,
        "total_turns": total_turns,
        "n_turns": n_turns,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="1-10",
                    help="Run-index range applied to both arms (default 1-10)")
    ap.add_argument("--gated-runs", default=None)
    ap.add_argument("--with-runs", default=None)
    ap.add_argument("--dir", default=str(CHAIN_DIR))
    args = ap.parse_args()

    chain_dir = Path(args.dir)
    if not chain_dir.exists():
        raise SystemExit(f"Chain dir not found: {chain_dir}")

    g_idx = parse_range(args.gated_runs or args.runs)
    w_idx = parse_range(args.with_runs or args.runs)

    print(f"Reading from: {chain_dir}")
    print(f"  gated_session indices: {g_idx}")
    print(f"  with_session indices:  {w_idx}\n")

    g_runs = load_runs(chain_dir, "gated_session", g_idx)
    w_runs = load_runs(chain_dir, "with_session", w_idx)

    g = summarize("gated_session", g_runs); print()
    w = summarize("with_session", w_runs); print()

    if not g or not w:
        return
    d_succ = g["mean_succ"] - w["mean_succ"]
    d_uncached = g["uncached_per_turn"] - w["uncached_per_turn"]
    d_cost = g["cost_per_run"] - w["cost_per_run"]
    pct_uncached = 100 * d_uncached / w["uncached_per_turn"] if w["uncached_per_turn"] else 0
    pct_cost = 100 * d_cost / w["cost_per_run"] if w["cost_per_run"] else 0
    print("=== delta (gated - with_session) ===")
    print(f"  d_success:           {d_succ:+.3f}  (gated sigma={g['sigma_succ']:.3f}, with sigma={w['sigma_succ']:.3f})")
    print(f"  d_uncached tok/turn: {d_uncached:+,.0f} ({pct_uncached:+.1f}%)")
    print(f"  d_$/run:             ${d_cost:+.3f} ({pct_cost:+.1f}%)")
    if g["mean_succ"] > 0 and w["mean_succ"] > 0:
        gps = g["cost_per_run"] / (g["mean_succ"] * g["n_turns"])
        wps = w["cost_per_run"] / (w["mean_succ"] * w["n_turns"])
        print(f"  $/success gated: ${gps:.3f}   $/success with: ${wps:.3f}   d ${gps-wps:+.3f}")


if __name__ == "__main__":
    main()
