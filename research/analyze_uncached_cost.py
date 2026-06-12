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
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agentic_variety_test import claude_cost, codex_cost  # noqa: E402

ARMS = ["no_session", "with_session", "with_gate", "slm_native", "stacked", "builtin", "gated_session"]
ARM_LABEL = {
    "no_session": "NO_SESSION (rewrite, no session)",
    "with_session": "WITH_SESSION (rewrite + bounded)",
    "with_gate": "WITH_GATE (rewrite + bounded + verify-gate)",
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
    per_run = {"success": [], "gross": [], "uncached": [], "cached": [], "cost": [], "calls": []}
    for rf in run_files:
        try:
            turns = json.loads(Path(rf).read_text(encoding="utf-8"))
        except Exception:
            continue
        s = g = unc = cac = cost = calls = 0.0
        for t in turns:
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
    if not per_run["success"]:
        return None
    n = len(per_run["success"])
    out = {k: statistics.mean(v) for k, v in per_run.items()}
    out["n_runs"] = n
    return out


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
    print("=" * 100)
    hdr = "  {:<34} {:>5} {:>7} {:>12} {:>12} {:>9} {:>10}".format(
        "arm", "runs", "succ", "gross_in", "UNCACHED", "$", "$/succ")
    print(hdr)
    print("  " + "-" * 96)
    for arm, st in rows:
        cps = ("{:>10.4f}".format(st["cost"] / st["success"])
               if st["success"] > 0 else "       n/a")
        print("  {:<34} {:>5d} {:>7.2f} {:>12,.0f} {:>12,.0f} {:>9.4f} {}".format(
            ARM_LABEL.get(arm, arm), st["n_runs"], st["success"],
            st["gross"], st["uncached"], st["cost"], cps))
    print("  " + "-" * 96)

    # Pairwise headline ratios when both arms present
    by = {arm: st for arm, st in rows}
    def ratio(a, b, key):
        if a in by and b in by and by[b][key]:
            return by[a][key] / by[b][key]
        return None
    pairs = [
        ("WITH_GATE vs WITH (verify-gate cost vs bounded, rewrite+session held constant)",
         "with_gate", "with_session"),
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
