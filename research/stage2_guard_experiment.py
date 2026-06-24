#!/usr/bin/env python
r"""Stage-2-LITE experiment (docs/SESSION_MEMORY_ROADMAP.md §6, gate OPEN).

QUESTION: does the additive-bias guard rewording reduce the destructive-migration
(orphan) rate vs the old "migrate to the new design" framing?

NON-CIRCULAR: the migration is performed by an SLM (the thing under test — the guard
text nudges it), but the OUTCOME is judged by REAL pytest on the oracle fixture
(research/_oracle_groundtruth.py's `kwarg-removed-destructive` before-code + test),
NEVER by an SLM judge. The migrator never sees the test BODY — only its NAME, exactly
as the live guard surfaces it.

DESIGN (isolates the framing variable). Fixed: before-code + test + task + model + N.
Variable: guard arm in {none, old, new}:
  - none : no guard line  -> the natural orphan rate of "consolidate the timeouts".
  - old  : the shipped-BEFORE wording (preserve-or-migrate / "migrate to the new design").
  - new  : the additive-bias reword (refactor_guard_checklist as shipped NOW).
old/new surface the SAME contract (connect_timeout, locked by test_connect_timeout_kwarg);
only the header + test-line label + refactor-framing line differ (the full reworded guard).

Per trial: SLM returns the new lib.py -> write it + the fixture test -> pytest.
  test_green (rc==0)  = the connect_timeout contract was PRESERVED (additive / correct migrate)
  test_red            = ORPHANED (kwarg dropped) OR the code broke (compiles flag separates these)
METRIC = green (preservation) rate per arm. Pre-registered hypothesis:
  green(new) > green(old), and ideally green(old) <= green(none) <= green(new).

Cost: N*3 small completions (default gpt-5.4-mini, ~cents). A tempdir per trial; read-only otherwise.
Usage:  python research/stage2_guard_experiment.py [N=12] [model=gpt-5.4-mini]
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _oracle_groundtruth as og  # noqa: E402  (before-code + test + the hardened _pytest)

# Reuse the oracle's kwarg fixture as the migration scenario (same before-code + test).
_CASE = next(c for c in og.ORPHAN_CASES if c["id"] == "kwarg-removed-destructive")
BEFORE_LIB = _CASE["before"]["lib.py"]
TEST_LIB = _CASE["before"]["test_lib.py"]

TASK = ("Refactor `make_client` so its timeout settings (timeout, connect_timeout) are configured "
        "through a single new `ResilienceConfig` dataclass.")

# old/new mirror refactor_guard_checklist's shipped-before vs reworded output (same contract surfaced).
GUARD = {
    "none": "",
    "old": (
        "[MEMORY — prior contracts you MUST preserve or intentionally migrate]\n"
        "- connect-timeout: make_client exposes a connect_timeout keyword argument.\n"
        "    tests/call-sites to keep or migrate: test_connect_timeout_kwarg\n"
        "This turn is a refactor/migration: do NOT silently drop the above — migrate their "
        "call-sites + tests to the new design, or explicitly state why each is removed."
    ),
    "new": (
        "[MEMORY — prior contracts to PRESERVE (prefer back-compat); migrate only if required]\n"
        "- connect-timeout: make_client exposes a connect_timeout keyword argument.\n"
        "    tests/call-sites that LOCK this — keep them GREEN: test_connect_timeout_kwarg\n"
        "This turn is a refactor/migration. PREFER ADDITIVE / back-compat: keep the existing public "
        "names above WORKING and ADD the new form alongside them. Remove a public name ONLY if truly "
        "required, and if you do you MUST update every listed call-site and test in THIS change so none "
        "are orphaned. Do not silently drop any of the above."
    ),
}
ARMS = ("none", "old", "new")


def _prompt(guard: str, i: int) -> str:
    g = ("\n" + guard + "\n") if guard else "\n"
    return (
        "You are refactoring a small Python module. Current lib.py:\n\n"
        "```python\n" + BEFORE_LIB + "```\n\n"
        "TASK (attempt {0}): {1}\n".format(i, TASK) + g +
        "\nReturn ONLY the complete new contents of lib.py inside one ```python code block — "
        "no prose, no test file."
    )


_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)


def _extract_code(text: str) -> str:
    m = _FENCE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def _compiles(code: str) -> bool:
    try:
        compile(code, "lib.py", "exec")
        return True
    except Exception:
        return False


def _score(code: str) -> tuple[bool, bool | None]:
    """(compiles, test_green). test_green None = pytest infra error (invalid trial)."""
    comp = _compiles(code)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "lib.py").write_text(code, encoding="utf-8")
        (root / "test_lib.py").write_text(TEST_LIB, encoding="utf-8")
        try:
            green = og._pytest(root)
        except Exception:
            return comp, None
    return comp, green


def run(n: int, model: str) -> None:
    import os
    from prpt.judges import OpenAiJudge
    if not os.environ.get("OPENAI_API_KEY"):
        try:
            from prpt.core.dotenv import load_dotenv
            import prpt
            root = Path(prpt.__file__).resolve().parent.parent  # editable-install root (B:/LLM) holds .env
            load_dotenv(root / ".env")
        except Exception as e:
            print("[env] load_dotenv failed: {0}".format(e), file=sys.stderr)
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set (env + prpt-root/.env). Aborting — no migrator.")

    judge = OpenAiJudge(model=model)
    probe_text, _pc, _ = judge("Reply with the single word: ok")
    if not (probe_text or "").strip():
        sys.exit("Migrator probe returned EMPTY (model={0}). Check the model name / API access.".format(model))
    print("Stage-2-lite guard experiment  (model={0}, N={1}/arm, oracle=kwarg fixture)".format(model, n))
    print("=" * 84)
    summary = {}
    total_cost = 0.0
    for arm in ARMS:
        green = comp = invalid = 0
        for i in range(1, n + 1):
            text, cost, _ = judge(_prompt(GUARD[arm], i))
            total_cost += float(cost or 0.0)
            code = _extract_code(text)
            c, g = _score(code)
            comp += int(c)
            if g is None:
                invalid += 1
            elif g:
                green += 1
            print(f"  {arm:4s} t{i:<2} compiles={int(c)} test_green={g}")
        valid = n - invalid
        rate = (green / valid) if valid else float("nan")
        summary[arm] = {"green": green, "valid": valid, "compiles": comp, "rate": rate}
        print(f"  -> {arm}: green {green}/{valid} = {rate:.2f}  (compiled {comp}/{n}; invalid {invalid})")

    print("\n" + "=" * 84)
    print("RESULT — green (connect_timeout-preserved) rate per arm:")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:4s}: {s['rate']:.2f}  (green {s['green']}/{s['valid']}, compiled {s['compiles']}/{n})")
    g_none, g_old, g_new = (summary[a]["rate"] for a in ARMS)
    print(f"\nHYPOTHESIS green(new) > green(old): {g_new:.2f} > {g_old:.2f} -> "
          f"{'SUPPORTED' if g_new > g_old else 'NOT supported'}")
    print(f"Old-guard-HURTS check green(old) <= green(none): {g_old:.2f} <= {g_none:.2f} -> "
          f"{'yes (old framing nudges destructive)' if g_old <= g_none else 'no'}")
    print(f"New-guard-HELPS check green(new) >= green(none): {g_new:.2f} >= {g_none:.2f} -> "
          f"{'yes' if g_new >= g_none else 'no'}")
    print(f"\nSLM cost: ${total_cost:.4f} ({n*len(ARMS)} completions). NON-CIRCULAR: outcome scored by "
          f"pytest, not an SLM. CAVEAT: single-shot SLM proxy on ONE kwarg scenario — a codex-agent "
          f"confirmation on more scenarios is the heavier follow-up.")


if __name__ == "__main__":
    _n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    _model = sys.argv[2] if len(sys.argv) > 2 else "gpt-5.4-mini"
    run(_n, _model)
