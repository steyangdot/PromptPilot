"""
judge_continuity.py — the H4 QUALITY instrument for the COMPACTION-REGIME TEST.

Design: docs/COMPACTION_REGIME_TEST.md  (§4.5)

Why this exists: the default scorers cannot see the H4 finding. `score_endstate.py`
ceilings at 1.000 and `score_turn` is a file-hash/churn detector — neither can detect
*continuity loss* (prpt's lossy memory_record vs native's lossy compaction) over a long
referential chain. So H4 needs a real judge.

What it does: for each arm/run it reads the harness-captured end state
(`endstate_{arm}_run{R}.json`, which holds the run's `git diff HEAD` + new files +
pytest result) and asks an LLM judge to rate, on a rubric derived from the chain_long
turns, whether the agent (a) implemented the full intended feature set and (b) correctly
resolved the heavily-referential refactor turns (the continuity signal). It then compares
`builtin` vs `with_session` so a continuity gap in either direction is visible.

This calls a model (the judge), so it costs a little — but it does NOT run the chain.
Judge is auto-selected via prpt's get_default_judge() / PROMPTPILOT_JUDGE.

Usage:
    python judge_continuity.py [OUT_DIR]
    # default OUT_DIR = research/data/chain_results_v2/codex/chain_long
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prpt.core.dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from prpt.judges import get_default_judge, extract_json  # noqa: E402
from chain_long_fixture import CHAIN_LONG  # noqa: E402

DIFF_CHAR_CAP = 60_000  # keep the judge prompt within a sane size

# The continuity rubric grades exactly these (tests carried forward, ResilienceConfig
# refactor, both clients migrated). `git diff` is path-ordered, so head-truncation
# drops `tests/` FIRST — the opposite of what we need (review #5). Float these to the
# front by TIER so the continuity evidence survives even if reference files alone
# exceed the cap. Tier 0 = the migration evidence the rubric weights most.
_TIER0 = ("/test", "tests/", "_config.py")                       # tests carried forward + ResilienceConfig
_TIER1 = ("_client.py", "_transports/default.py", "_stats.py",   # other reference files
          "changelog", "docs/resilience")


def _prioritize_diff(diff: str, cap: int) -> str:
    """Reorder a unified diff so continuity-critical files come first, then truncate.
    Keeps whole per-file chunks; reports any omission rather than silently cutting."""
    chunks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    chunks = [c for c in chunks if c.strip()]
    if len(chunks) <= 1:
        # not a multi-file diff we can reorder; fall back to head truncation
        return diff[:cap] + ("\n...[diff truncated]..." if len(diff) > cap else "")

    def tier(c: str) -> int:
        header = c.splitlines()[0].lower() if c else ""
        if any(p in header for p in _TIER0):
            return 0
        if any(p in header for p in _TIER1):
            return 1
        return 2

    # stable sort by tier preserves path order within a tier
    ordered = sorted(chunks, key=tier)
    kept, used, omitted = [], 0, 0
    for c in ordered:
        if used + len(c) <= cap:
            kept.append(c)
            used += len(c)
        else:
            omitted += 1
    note = ("\n...[{0} file-diffs omitted for length; reference-critical files were "
            "prioritized first]...".format(omitted)) if omitted else ""
    return "".join(kept) + note


def _turn_summary() -> str:
    lines = []
    for i, t in enumerate(CHAIN_LONG["turns"], 1):
        ref = " [refers back]" if t.get("referential") else ""
        lines.append("  T{0}{1}: {2}".format(i, ref, t["raw"]))
    return "\n".join(lines)


def _build_prompt(diff: str, new_files, pytest_passed) -> str:
    truncated = _prioritize_diff(diff, DIFF_CHAR_CAP)
    return (
        "You are grading whether a coding agent completed a 24-step DEPENDENT task on the "
        "httpx codebase and preserved CONTINUITY across back-references between steps.\n\n"
        "The 24 steps (each builds on earlier ones; '[refers back]' = it depends on prior steps):\n"
        + _turn_summary()
        + "\n\nThe agent's FINAL git diff (the cumulative result of all 24 steps):\n"
        + "----- BEGIN DIFF -----\n" + truncated + "\n----- END DIFF -----\n"
        + "new files created: {0}\n".format(new_files)
        + "timeout regression pytest passed: {0}\n\n".format(pytest_passed)
        + "Grade on this rubric and return ONLY JSON:\n"
        + "{\n"
        + '  "implemented": <0.0-1.0: fraction of the intended feature set actually present in the diff '
          '(timeout overrides, retry-after incl. http-date + delay cap, request timing + stats, pool size + '
          'exhaustion warning)>,\n'
        + '  "continuity": <0.0-1.0: did the heavily-referential late steps resolve correctly — '
          'a single ResilienceConfig that unifies the timeout/retry/pool overrides AND both sync+async '
          'clients migrated to it AND the earlier tests updated to it? 1.0 = fully resolved, '
          '0.0 = the references were lost / the refactor does not reflect the earlier work>,\n'
        + '  "tests_updated": <0.0-1.0: were the tests written in earlier steps carried forward / migrated>,\n'
        + '  "evidence": "<one or two sentences citing what in the diff supports the scores>"\n'
        + "}\n"
    )


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).parent / "data" / "chain_results_v2" / "codex" / "chain_long")
    if not out_dir.exists():
        raise SystemExit("OUT_DIR not found: {0}".format(out_dir))

    judge = get_default_judge()
    print("[judge] using: {0}".format(getattr(judge, "name", type(judge).__name__)))

    pat = re.compile(r"^endstate_(.+)_run(\d+)\.json$")
    endstates = defaultdict(dict)  # arm -> {run -> path}
    for p in out_dir.glob("endstate_*_run*.json"):
        m = pat.match(p.name)
        if m:
            endstates[m.group(1)][int(m.group(2))] = p
    if not endstates:
        raise SystemExit("No endstate_<arm>_run<N>.json files in {0}".format(out_dir))

    results = defaultdict(list)   # arm -> list of verdict dicts
    report = []
    def out(s=""):
        print(s)
        report.append(s)

    out("=" * 72)
    out("H4 CONTINUITY JUDGE — {0}".format(out_dir))
    out("=" * 72)

    for arm in sorted(endstates):
        out("\nARM: {0}".format(arm))
        for run in sorted(endstates[arm]):
            try:
                es = json.loads(endstates[arm][run].read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                out("  run{0}: [skip] could not read endstate ({1})".format(run, e))
                continue
            diff = es.get("diff") or ""
            if not diff.strip():
                out("  run{0}: [skip] empty diff".format(run))
                continue
            prompt = _build_prompt(diff, es.get("new_files"), es.get("pytest_passed"))
            try:
                text, cost, _ = judge(prompt, timeout=120)
                verdict = extract_json(text) or {}
            except Exception as e:
                out("  run{0}: [error] judge failed: {1}".format(run, e))
                continue
            verdict["_run"] = run
            results[arm].append(verdict)
            out("  run{0}: implemented={1} continuity={2} tests_updated={3}  — {4}".format(
                run, verdict.get("implemented"), verdict.get("continuity"),
                verdict.get("tests_updated"), (verdict.get("evidence") or "")[:160]))

    out("\n" + "=" * 72)
    out("SUMMARY (mean per arm)")
    def _mean(arm, key):
        xs = [v[key] for v in results[arm] if isinstance(v.get(key), (int, float))]
        return statistics.mean(xs) if xs else None
    for arm in sorted(results):
        out("  {0:14s} implemented={1} continuity={2} tests_updated={3}  (n={4})".format(
            arm, _mean(arm, "implemented"), _mean(arm, "continuity"),
            _mean(arm, "tests_updated"), len(results[arm])))

    if "builtin" in results and "with_session" in results:
        bc = _mean("builtin", "continuity")
        wc = _mean("with_session", "continuity")
        if bc is not None and wc is not None:
            out("")
            gap = wc - bc
            if abs(gap) < 0.1:
                out("  CONTINUITY: with_session ~= builtin (|gap|<0.1) — bounded memory holds "
                    "parity against native compaction at length.")
            elif gap < 0:
                out("  CONTINUITY GAP: with_session {0:.2f} < builtin {1:.2f} — prpt's lossy "
                    "memory loses continuity vs native compaction (H4 finding; investigate the "
                    "diverging runs by hand).".format(wc, bc))
            else:
                out("  CONTINUITY: with_session {0:.2f} > builtin {1:.2f} — bounded memory keeps "
                    "MORE continuity than native compaction.".format(wc, bc))

    rpt = out_dir / "CONTINUITY_JUDGE.md"
    rpt.write_text("```\n" + "\n".join(report) + "\n```\n", encoding="utf-8")
    print("\n[written] {0}".format(rpt))


if __name__ == "__main__":
    main()
