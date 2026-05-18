"""Chain5 long-task decay analysis (FIX_PLAN P3 #9 / SLM-harness Step 1).

Reads per-turn JSONs under chain_results_v2/claude-code/chain5/ (Sonnet default)
or chain_results_v2_opus/ (Opus) and produces four cuts:

  1. Per-turn success and tokens (raw position-by-position view)
  2. Fresh-mod decay anchors (T1/T2/T5/T9) — pure position decay
  3. Explain decay (T4/T8/T13) — read-only baseline
  4. Success vs reference distance (T3/T6/T7/T10/T11/T12/T14/T15)

If T1/T2 succeed and T9 fails on the same kind of work, that's pure decay.
If d=1 refs succeed but d=14 refs fail, that's window eviction.
If both happen, the SLM-harness ideas (critique loop / decomposition) have
something to bite on; if neither, the long-task quality premise was wrong.

    python analyze_chain5_decay.py
    python analyze_chain5_decay.py --variant with_session --runs 1-3
    python analyze_chain5_decay.py --dir chain_results_v2_opus --variant with_session
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

# Chain5 turn metadata — must stay in sync with CHAINS["chain5"] in chain_test_v2.py
# (idx is 1-based to match T1..T15 labelling; ref_distance is None for non-ref turns)
TURN_META: list[tuple[int, str, int | None]] = [
    (1,  "fresh",   None),
    (2,  "fresh",   None),
    (3,  "ref",     2),    # → T1
    (4,  "explain", None),
    (5,  "fresh",   None),
    (6,  "ref",     1),    # → T5
    (7,  "ref",     6),    # → T1
    (8,  "explain", None),
    (9,  "fresh",   None),
    (10, "ref",     1),    # → T9
    (11, "ref",     6),    # → T5/T6
    (12, "ref",     11),   # → T1
    (13, "explain", None),
    (14, "ref",     5),    # → T9/T10
    (15, "ref",     14),   # → T1
]


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


def _check_model_consistency(label: str, runs: list[list[dict]]) -> None:
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


def per_turn_success(runs: list[list[dict]]) -> list[float]:
    n_turns = len(runs[0])
    return [statistics.mean(r[t]["score"]["success"] for r in runs) for t in range(n_turns)]


def per_turn_tokens(runs: list[list[dict]], key: str) -> list[float]:
    n_turns = len(runs[0])
    return [statistics.mean(r[t]["usage"].get(key, 0) for r in runs) for t in range(n_turns)]


def per_turn_uncached(runs: list[list[dict]]) -> list[float]:
    n_turns = len(runs[0])
    out: list[float] = []
    for t in range(n_turns):
        vals = [r[t]["usage"]["input_tokens"] - r[t]["usage"].get("cached_tokens", 0) for r in runs]
        out.append(statistics.mean(vals))
    return out


def cut1_per_turn(succ: list[float], intok: list[float], uncached: list[float], outtok: list[float]) -> None:
    print("=== Cut 1: per-turn position-by-position view ===")
    print(f"{'T':>3}  {'kind':7s}  {'ref_d':>5s}  {'success':>7s}  {'in_tok':>8s}  {'uncached':>8s}  {'out_tok':>7s}")
    for (idx, kind, dist), s, it, uc, ot in zip(TURN_META, succ, intok, uncached, outtok):
        d_str = str(dist) if dist is not None else "—"
        print(f"T{idx:<2d}  {kind:7s}  {d_str:>5s}  {s:>7.3f}  {it:>8,.0f}  {uc:>8,.0f}  {ot:>7,.0f}")
    print()


def cut2_fresh_decay(succ: list[float]) -> None:
    print("=== Cut 2: fresh-mod decay anchors (same task type, varying position) ===")
    print("If success drops from T1 → T9, that's pure position decay (independent of references).")
    print(f"{'T':>3}  {'position':>8s}  {'success':>7s}")
    for (idx, kind, _), s in zip(TURN_META, succ):
        if kind == "fresh":
            print(f"T{idx:<2d}  {idx:>8d}  {s:>7.3f}")
    fresh = [s for (i, k, _), s in zip(TURN_META, succ) if k == "fresh"]
    if len(fresh) >= 2:
        print(f"  delta T1→last: {fresh[-1] - fresh[0]:+.3f}")
    print()


def cut3_explain_decay(succ: list[float]) -> None:
    print("=== Cut 3: explain (read-only) baseline at positions 4 / 8 / 13 ===")
    print("If these decay, the issue is more general than session-window eviction.")
    print(f"{'T':>3}  {'position':>8s}  {'success':>7s}")
    for (idx, kind, _), s in zip(TURN_META, succ):
        if kind == "explain":
            print(f"T{idx:<2d}  {idx:>8d}  {s:>7.3f}")
    print()


def cut4_distance(succ: list[float]) -> None:
    print("=== Cut 4: success vs reference distance (referential turns only) ===")
    print("If success cliffs at d > N, that's window eviction — the headline result for SLM-harness ideas.")
    by_dist: dict[int, list[tuple[int, float]]] = {}
    for (idx, kind, dist), s in zip(TURN_META, succ):
        if kind == "ref" and dist is not None:
            by_dist.setdefault(dist, []).append((idx, s))
    print(f"{'distance':>8s}  {'N':>3s}  {'mean':>7s}  turns")
    for dist in sorted(by_dist):
        entries = by_dist[dist]
        mean_s = statistics.mean(s for _, s in entries)
        turns_str = ", ".join(f"T{i}({s:.2f})" for i, s in entries)
        print(f"{dist:>8d}  {len(entries):>3d}  {mean_s:>7.3f}  {turns_str}")
    print()


def overall(label: str, runs: list[list[dict]]) -> None:
    n = len(runs)
    n_turns = len(runs[0])
    run_means = [statistics.mean(t["score"]["success"] for t in r) for r in runs]
    run_costs = [sum(t["usage"]["total_cost_usd"] + t.get("slm_cost", 0) for t in r) for r in runs]
    overall_succ = statistics.mean(run_means)
    sigma = statistics.stdev(run_means) if n >= 2 else 0.0
    print(f"=== Overall [{label}] ===")
    print(f"  N={n} runs x {n_turns} turns")
    print(f"  per-run success means: {[round(x, 3) for x in run_means]}")
    print(f"  per-run cost: {[f'${c:.3f}' for c in run_costs]}")
    print(f"  overall success: {overall_succ:.3f}  sigma={sigma:.3f}")
    print(f"  total cost: ${sum(run_costs):.2f}  (mean ${statistics.mean(run_costs):.3f}/run)")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="chain_results_v2",
                    help="Results dir (chain_results_v2 for Sonnet, chain_results_v2_opus for Opus)")
    ap.add_argument("--variant", default="with_session",
                    help="Variant to analyze (default: with_session)")
    ap.add_argument("--runs", default="1-10",
                    help="Run-index range (default 1-10; missing files are skipped with a warning)")
    args = ap.parse_args()

    chain_dir = Path(__file__).parent / args.dir / "claude-code" / "chain5"
    if not chain_dir.exists():
        raise SystemExit(f"Chain dir not found: {chain_dir}")

    indices = parse_range(args.runs)
    print(f"Reading from: {chain_dir}")
    print(f"  variant: {args.variant}")
    print(f"  run indices: {indices}\n")

    runs = load_runs(chain_dir, args.variant, indices)
    if not runs:
        raise SystemExit("No runs loaded — nothing to analyze.")
    if len(runs[0]) != len(TURN_META):
        raise SystemExit(
            f"Turn-count mismatch: TURN_META has {len(TURN_META)} entries but "
            f"runs have {len(runs[0])} turns. Did chain5 definition change?"
        )

    _check_model_consistency(args.variant, runs)

    overall(args.variant, runs)
    succ = per_turn_success(runs)
    intok = per_turn_tokens(runs, "input_tokens")
    uncached = per_turn_uncached(runs)
    outtok = per_turn_tokens(runs, "output_tokens")

    cut1_per_turn(succ, intok, uncached, outtok)
    cut2_fresh_decay(succ)
    cut3_explain_decay(succ)
    cut4_distance(succ)


if __name__ == "__main__":
    main()
