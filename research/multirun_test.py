"""
Multi-run conversation test: measure token savings from session history.

Compares two variants for turn 2 of a conversation:
  NO_SESSION  — turn 2 sent standalone, no prior context
  WITH_SESSION — turn 1 recorded in session, turn 2 gets [Recent conversation] prepend

Scenarios:
  A: Referential follow-up ("that fix")     — biggest expected benefit
  B: Cascading feature refinement           — moderate benefit
  C: Topic switch (unrelated turn 2)        — minimal effect expected (control)

Usage:
    python multirun_test.py --dry-run                  # show prompts, no execution
    python multirun_test.py --tool claude-code          # run full comparison
    python multirun_test.py --scenario a --tool codex  # single scenario
    python multirun_test.py --reprint                  # re-display saved results
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from prpt.normalizers.base import build_final_downstream_prompt, build_output_suffix, create_normalizer
from prpt.repo.collector import RepoContextCollector
from prpt.session import append_turn, clear_session, load_recent_turns

# Reuse infrastructure from the existing test runner
from agentic_variety_test import (
    _cost, _ext, _parse_one, _run_one,
    claude_cost, codex_cost, slm_cost_estimate,
)

HTTPX_DIR = "C:/projects/httpx"
OUT_DIR = Path(__file__).parent / "multirun_results"

SCENARIOS = [
    {
        "id": "a",
        "label": "Referential follow-up — 'that fix'",
        "turn1": "fix the timeout not being passed through to the underlying socket in the sync client",
        "turn2": "now add a unit test for that fix",
        "expected": "WITH_SESSION resolves 'that fix' → precise test target → fewer output tokens",
    },
    {
        "id": "b",
        "label": "Cascading feature refinement",
        "turn1": "add retry-after header support to the retry logic",
        "turn2": "also handle the edge case where retry-after value is a date string instead of seconds",
        "expected": "WITH_SESSION knows retry-after feature location → localized scope → fewer tokens",
    },
    {
        "id": "c",
        "label": "Topic switch (control — history should be ignored)",
        "turn1": "fix the timeout not being passed through to the underlying socket in the sync client",
        "turn2": "explain how redirects are handled in the client",
        "expected": "Minimal difference — turn 2 is unrelated to turn 1",
    },
]


# ---------------------------------------------------------------------------
# Prompt preparation
# ---------------------------------------------------------------------------

def _build_normalizer():
    return create_normalizer("slm", load_repo_content=True)


def prepare_no_session(turn2_raw: str, cwd: str, tool: str) -> dict:
    """Prepare turn 2 as a standalone prompt — no history."""
    repo = RepoContextCollector().collect(cwd)
    normalizer = _build_normalizer()
    normalized = normalizer.normalize(turn2_raw, repo)
    grounded = build_final_downstream_prompt(normalized, repo)
    intent = getattr(normalizer, "_last_intent", "act")
    scope  = getattr(normalizer, "_last_scope", "localized")
    suffix_tool = "anthropic" if tool == "claude-code" else tool
    suffix = build_output_suffix(scope, suffix_tool) if intent == "act" else ""
    optimized = grounded + "\n\n" + suffix if suffix else grounded
    return {
        "raw": turn2_raw,
        "grounded": grounded,
        "optimized": optimized,
        "intent": intent,
        "scope": scope,
        "suffix": suffix,
        "variant": "NO_SESSION",
    }


def prepare_with_session(turn1_raw: str, turn2_raw: str, cwd: str, tool: str) -> dict:
    """Record turn 1 in session, then prepare turn 2 with history prepend."""
    # Simulate what the CLI records after executing turn 1:
    # Run turn 1 through normalizer to get its rewrite (for the assistant turn)
    repo = RepoContextCollector().collect(cwd)
    norm1 = _build_normalizer()
    norm1_result = norm1.normalize(turn1_raw, repo)
    turn1_rewrite = norm1_result.normalized_prompt[:600]

    # Write both turns to session (as CLI would after execution)
    clear_session(cwd)
    append_turn(cwd, "user",      turn1_raw)
    append_turn(cwd, "assistant", turn1_rewrite)

    # Load history and build the prepended prompt for turn 2
    recent = load_recent_turns(cwd)
    history = "\n".join(recent)
    prompt_with_history = (
        "[Recent conversation]\n{history}\n\n[Current request]\n{prompt}"
    ).format(history=history, prompt=turn2_raw)

    # Normalize turn 2 with history
    normalizer = _build_normalizer()
    normalized = normalizer.normalize(prompt_with_history, repo)
    grounded = build_final_downstream_prompt(normalized, repo)
    intent = getattr(normalizer, "_last_intent", "act")
    scope  = getattr(normalizer, "_last_scope", "localized")
    suffix_tool = "anthropic" if tool == "claude-code" else tool
    suffix = build_output_suffix(scope, suffix_tool) if intent == "act" else ""
    optimized = grounded + "\n\n" + suffix if suffix else grounded

    return {
        "raw": turn2_raw,
        "grounded": grounded,
        "optimized": optimized,
        "intent": intent,
        "scope": scope,
        "suffix": suffix,
        "variant": "WITH_SESSION",
        "history_prepend": prompt_with_history,
        "turn1_rewrite": turn1_rewrite,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_scenario_header(scenario: dict) -> None:
    print()
    print("=" * 72)
    print("  Scenario {id}: {label}".format(**scenario))
    print("  Turn 1: {0}".format(scenario["turn1"]))
    print("  Turn 2: {0}".format(scenario["turn2"]))
    print("  Expected: {0}".format(scenario["expected"]))
    print("=" * 72)


def print_prompts(no_sess: dict, with_sess: dict) -> None:
    print()
    print("  --- NO_SESSION (turn 2 standalone) ---")
    print("  intent={intent}  scope={scope}".format(**no_sess))
    print("  Grounded prompt ({0} chars):".format(len(no_sess["grounded"])))
    preview = no_sess["grounded"][:400]
    for line in preview.splitlines():
        print("    " + line)
    if len(no_sess["grounded"]) > 400:
        print("    ...")
    print()
    print("  --- WITH_SESSION (turn 2 with history) ---")
    print("  intent={intent}  scope={scope}".format(**with_sess))
    print("  Grounded prompt ({0} chars):".format(len(with_sess["grounded"])))
    preview = with_sess["grounded"][:400]
    for line in preview.splitlines():
        print("    " + line)
    if len(with_sess["grounded"]) > 400:
        print("    ...")
    print()


def print_results(scenario: dict, no_sess: dict, with_sess: dict,
                  no_u: dict, with_u: dict,
                  no_t: float, with_t: float, tool: str) -> None:
    print_scenario_header(scenario)

    no_cost   = _cost(no_u,   tool)
    with_cost = _cost(with_u, tool)
    no_slm    = slm_cost_estimate(no_sess["raw"],   no_sess["grounded"])
    with_slm  = slm_cost_estimate(with_sess["raw"], with_sess["grounded"])

    hdr = "  {:<22} {:>14} {:>14} {:>12}".format("Metric", "NO_SESSION", "WITH_SESSION", "Δ")
    print(hdr)
    print("  " + "-" * 64)

    def row(name, a, b):
        d = b - a
        sign = "+" if d >= 0 else ""
        print("  {:<22} {:>14,} {:>14,} {:>12}".format(
            name, a, b, "{0}{1:,}".format(sign, d)))

    def cost_row(name, a, b):
        d = b - a
        sign = "+" if d >= 0 else ""
        print("  {:<22} {:>14.4f} {:>14.4f} {:>12}".format(
            name, a, b, "{0}{1:.4f}".format(sign, d)))

    row("Input tokens",   no_u["input_tokens"],  with_u["input_tokens"])
    row("Cached tokens",  no_u["cached_tokens"], with_u["cached_tokens"])
    row("Output tokens",  no_u["output_tokens"], with_u["output_tokens"])
    row("Total tokens",
        no_u["input_tokens"]   + no_u["output_tokens"],
        with_u["input_tokens"] + with_u["output_tokens"])
    row("Tool calls",     no_u["tool_calls"],    with_u["tool_calls"])
    print("  " + "-" * 64)
    cost_label = "Claude cost ($)" if tool == "claude-code" else "Codex cost ($)"
    cost_row(cost_label, no_cost, with_cost)
    print("  {:<22} {:>14.4f} {:>14.4f}".format("SLM cost ($)", no_slm, with_slm))
    net_no   = no_cost   + no_slm
    net_with = with_cost + with_slm
    cost_row("Total cost ($)",  net_no, net_with)
    print("  " + "-" * 64)
    print("  {:<22} {:>13.1f}s {:>13.1f}s".format("Wall-clock (s)", no_t, with_t))

    out_no   = no_u["output_tokens"]
    out_with = with_u["output_tokens"]
    print()
    if out_no > 0:
        pct = (out_no - out_with) / out_no * 100
        sign = "-" if pct >= 0 else "+"
        print("  OUTPUT TOKEN CHANGE (WITH_SESSION vs NO_SESSION): {0:+.1f}%".format(-pct))
        if pct > 5:
            print("  => session history reduced output tokens (reference resolved precisely)")
        elif pct < -5:
            print("  => session history increased output tokens (history expanded scope)")
        else:
            print("  => negligible difference (history irrelevant or already self-contained)")
    savings = net_no - net_with
    print("  NET COST CHANGE: {0:+.4f} USD (negative = WITH_SESSION is cheaper)".format(-savings))
    print()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict, tool: str) -> None:
    sid = scenario["id"]
    ext = _ext(tool)
    OUT_DIR.mkdir(exist_ok=True)
    tool_dir = OUT_DIR / tool
    tool_dir.mkdir(exist_ok=True)

    no_out   = tool_dir / "scenario_{0}_no_session{1}".format(sid, ext)
    with_out = tool_dir / "scenario_{0}_with_session{1}".format(sid, ext)

    print_scenario_header(scenario)

    print("\n  Preparing NO_SESSION prompts...")
    clear_session(HTTPX_DIR)
    no_sess = prepare_no_session(scenario["turn2"], HTTPX_DIR, tool)

    print("  Preparing WITH_SESSION prompts (running turn 1 through SLM)...")
    with_sess = prepare_with_session(scenario["turn1"], scenario["turn2"], HTTPX_DIR, tool)
    clear_session(HTTPX_DIR)  # clean up after test

    print_prompts(no_sess, with_sess)

    print("  Running NO_SESSION through {0}...".format(tool))
    no_t, _ = _run_one(no_sess["optimized"], no_out, HTTPX_DIR, tool)
    print("  Done {0:.1f}s".format(no_t))

    print("  Running WITH_SESSION through {0}...".format(tool))
    with_t, _ = _run_one(with_sess["optimized"], with_out, HTTPX_DIR, tool)
    print("  Done {0:.1f}s".format(with_t))

    no_u   = _parse_one(no_out,   tool)
    with_u = _parse_one(with_out, tool)

    # Save summary
    summary = {
        "scenario": sid,
        "tool": tool,
        "no_session": {**no_u, "prompt_chars": len(no_sess["optimized"]),
                       "scope": no_sess["scope"], "intent": no_sess["intent"]},
        "with_session": {**with_u, "prompt_chars": len(with_sess["optimized"]),
                         "scope": with_sess["scope"], "intent": with_sess["intent"]},
    }
    (tool_dir / "scenario_{0}_summary.json".format(sid)).write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print_results(scenario, no_sess, with_sess, no_u, with_u, no_t, with_t, tool)


def dry_run_scenario(scenario: dict, tool: str) -> None:
    print_scenario_header(scenario)

    print("\n  Preparing NO_SESSION prompts...")
    clear_session(HTTPX_DIR)
    no_sess = prepare_no_session(scenario["turn2"], HTTPX_DIR, tool)

    print("  Preparing WITH_SESSION prompts...")
    with_sess = prepare_with_session(scenario["turn1"], scenario["turn2"], HTTPX_DIR, tool)
    clear_session(HTTPX_DIR)

    print_prompts(no_sess, with_sess)
    print("  intent/scope  NO_SESSION:   {intent} / {scope}".format(**no_sess))
    print("  intent/scope  WITH_SESSION: {intent} / {scope}".format(**with_sess))
    print("  Prompt chars  NO_SESSION:   {0}".format(len(no_sess["optimized"])))
    print("  Prompt chars  WITH_SESSION: {0}".format(len(with_sess["optimized"])))


def reprint_scenario(scenario: dict, tool: str) -> None:
    sid = scenario["id"]
    ext = _ext(tool)
    tool_dir = OUT_DIR / tool
    no_out   = tool_dir / "scenario_{0}_no_session{1}".format(sid, ext)
    with_out = tool_dir / "scenario_{0}_with_session{1}".format(sid, ext)

    if not no_out.exists():
        print("  [skip] no saved results for scenario {0}/{1}".format(sid, tool))
        return

    clear_session(HTTPX_DIR)
    no_sess   = prepare_no_session(scenario["turn2"], HTTPX_DIR, tool)
    with_sess = prepare_with_session(scenario["turn1"], scenario["turn2"], HTTPX_DIR, tool)
    clear_session(HTTPX_DIR)

    no_u   = _parse_one(no_out,   tool)
    with_u = _parse_one(with_out, tool) if with_out.exists() else no_u
    print_results(scenario, no_sess, with_sess, no_u, with_u, 0.0, 0.0, tool)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="all",
                        choices=["a", "b", "c", "all"],
                        help="Which scenario to run (default: all)")
    parser.add_argument("--tool", default="claude-code",
                        choices=["codex", "claude-code"],
                        help="Downstream tool (default: claude-code)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prepared prompts without running the tool")
    parser.add_argument("--reprint", action="store_true",
                        help="Re-display saved results without re-running")
    args = parser.parse_args()

    targets = SCENARIOS if args.scenario == "all" else [
        s for s in SCENARIOS if s["id"] == args.scenario
    ]

    for scenario in targets:
        if args.dry_run:
            dry_run_scenario(scenario, args.tool)
        elif args.reprint:
            reprint_scenario(scenario, args.tool)
        else:
            run_scenario(scenario, args.tool)

    if not args.dry_run:
        print("\nOutput files:", OUT_DIR / args.tool)


if __name__ == "__main__":
    main()
