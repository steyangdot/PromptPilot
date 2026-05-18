"""
Multi-turn chain test: measure token savings over 5-turn conversation chains.

Three chains:
  1: Bug fix → test → async mirror → async test → inline comment
  2: Feature add → date-string edge case → delay cap → tests → async mirror
  3: Explain → refactor → extract method → type hints → unit test

For each chain, every turn is run two ways:
  NO_SESSION  — turn sent standalone with no prior context
  WITH_SESSION — turns accumulate in session; each turn gets [Recent conversation] prepend

Turn 1 is identical for both (no session yet) and serves as the baseline.

Usage:
    python chain_test.py --dry-run
    python chain_test.py --chain 1 --tool claude-code
    python chain_test.py --tool codex
    python chain_test.py --reprint
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from pathlib import Path

# Force UTF-8 stdout on Windows to avoid GBK encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from prpt.normalizers.base import build_final_downstream_prompt, build_output_suffix, create_normalizer
from prpt.repo.collector import RepoContextCollector
from prpt.session import append_turn, clear_session, load_recent_turns

from agentic_variety_test import (
    _cost, _ext, _parse_one, _run_one,
    claude_cost, codex_cost, slm_cost_estimate,
)

HTTPX_DIR = "C:/projects/httpx"
OUT_DIR = Path(__file__).parent / "chain_results"


def reset_repo(cwd: str) -> None:
    """Reset the repo to HEAD — undo all tracked changes and remove untracked files."""
    subprocess.run(["git", "checkout", "--", "."], cwd=cwd, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=cwd, capture_output=True)
    print("  [git] repo reset to HEAD")

CHAINS = [
    {
        "id": "chain1",
        "label": "Bug fix workflow (referential throughout)",
        "description": (
            "Fixes a bug, then adds tests, mirrors to async, adds async test, "
            "then adds inline comments. Every turn after turn 1 is referential."
        ),
        "turns": [
            "fix the timeout not being passed through to the underlying socket in the sync client",
            "add a unit test for that fix",
            "apply the same fix to the async client",
            "add a unit test for the async fix as well",
            "add a brief inline comment to both fixes explaining why the timeout must be passed explicitly",
        ],
    },
    {
        "id": "chain2",
        "label": "Feature addition workflow (progressive elaboration)",
        "description": (
            "Adds a feature, then handles an edge case, adds a cap, writes tests, "
            "then mirrors to async. Increasing specificity each turn."
        ),
        "turns": [
            "add retry-after header support to the retry logic",
            "extend that to also handle retry-after as a date string not just seconds",
            "cap the retry delay at 60 seconds to prevent excessive waits",
            "write tests covering all three retry-after scenarios we just added",
            "mirror all of those changes to the async client",
        ],
    },
    {
        "id": "chain3",
        "label": "Exploratory then targeted (explain → refactor chain)",
        "description": (
            "Starts with an explain prompt, then a series of targeted refactors "
            "based on the explanation. Tests explain→act context handoff."
        ),
        "turns": [
            "explain how the connection pool manages connection lifecycle",
            "the connection validation looks inefficient, refactor it",
            "extract the validation logic into a separate _validate_connection method",
            "add type hints to that new method",
            "write a unit test for the extracted method",
        ],
    },
]


# ---------------------------------------------------------------------------
# Prompt preparation
# ---------------------------------------------------------------------------

def _make_normalizer():
    return create_normalizer("slm", load_repo_content=True)


def _optimize(raw: str, cwd: str, tool: str, normalizer=None) -> dict:
    """Run SLM on raw prompt, return prepared dict. Uses provided normalizer if given."""
    repo = RepoContextCollector().collect(cwd)
    norm = normalizer or _make_normalizer()
    normalized = norm.normalize(raw, repo)
    grounded = build_final_downstream_prompt(normalized, repo)
    intent = getattr(norm, "_last_intent", "act")
    scope  = getattr(norm, "_last_scope", "localized")
    suffix_tool = "anthropic" if tool == "claude-code" else tool
    suffix = build_output_suffix(scope, suffix_tool) if intent == "act" else ""
    optimized = grounded + "\n\n" + suffix if suffix else grounded
    return {
        "raw": raw,
        "rewrite": normalized.normalized_prompt,
        "grounded": grounded,
        "optimized": optimized,
        "intent": intent,
        "scope": scope,
        "suffix": suffix,
    }


def prepare_no_session_turn(raw: str, cwd: str, tool: str) -> dict:
    """Prepare a single turn with no session history.
    Does NOT touch the session — caller manages session state.
    """
    result = _optimize(raw, cwd, tool)
    result["variant"] = "NO_SESSION"
    return result


def prepare_with_session_turn(raw: str, cwd: str, tool: str) -> dict:
    """Prepare a single turn using whatever is currently in the session.
    Does NOT record to session — caller must call record_to_session() after.
    """
    recent = load_recent_turns(cwd)
    if recent:
        history = "\n".join(recent)
        prompt_for_slm = (
            "[Recent conversation]\n{history}\n\n[Current request]\n{prompt}"
        ).format(history=history, prompt=raw)
    else:
        prompt_for_slm = raw
    result = _optimize(prompt_for_slm, cwd, tool)
    result["raw"] = raw
    result["variant"] = "WITH_SESSION"
    result["had_history"] = bool(recent)
    return result


def record_to_session(cwd: str, raw: str, rewrite: str) -> None:
    """Append a completed turn to the session transcript."""
    append_turn(cwd, "user",      raw)
    append_turn(cwd, "assistant", rewrite[:600])


# ---------------------------------------------------------------------------
# Chain runners
# ---------------------------------------------------------------------------

def run_chain_no_session(chain: dict, tool: str, out_dir: Path) -> list[dict]:
    """Run all turns standalone (no session). Returns per-turn results."""
    clear_session(HTTPX_DIR)  # ensure clean state before no-session phase
    results = []
    ext = _ext(tool)
    for i, raw in enumerate(chain["turns"], 1):
        print("  [no_session] Turn {0}: {1}...".format(i, raw[:60]))
        out_path = out_dir / "t{0}_no_session{1}".format(i, ext)
        prepared = prepare_no_session_turn(raw, HTTPX_DIR, tool)
        t, _ = _run_one(prepared["optimized"], out_path, HTTPX_DIR, tool)
        usage = _parse_one(out_path, tool)
        slm_cost = slm_cost_estimate(raw, prepared["grounded"])
        results.append({
            "turn": i,
            "raw": raw,
            "scope": prepared["scope"],
            "intent": prepared["intent"],
            "prompt_chars": len(prepared["optimized"]),
            "had_history": False,
            "usage": usage,
            "slm_cost": slm_cost,
            "wall_t": t,
        })
        print("    output_tokens={0}  tool_calls={1}  {2:.1f}s".format(
            usage["output_tokens"], usage["tool_calls"], t))
    return results


def run_chain_with_session(chain: dict, tool: str, out_dir: Path) -> list[dict]:
    """Run all turns with accumulating session history. Returns per-turn results."""
    clear_session(HTTPX_DIR)
    results = []
    ext = _ext(tool)
    for i, raw in enumerate(chain["turns"], 1):
        print("  [with_session] Turn {0}: {1}...".format(i, raw[:60]))
        out_path = out_dir / "t{0}_with_session{1}".format(i, ext)
        prepared = prepare_with_session_turn(raw, HTTPX_DIR, tool)
        t, _ = _run_one(prepared["optimized"], out_path, HTTPX_DIR, tool)
        usage = _parse_one(out_path, tool)
        slm_cost = slm_cost_estimate(raw, prepared["grounded"])
        # Record this turn to session before next turn
        record_to_session(HTTPX_DIR, raw, prepared["rewrite"])
        results.append({
            "turn": i,
            "raw": raw,
            "scope": prepared["scope"],
            "intent": prepared["intent"],
            "prompt_chars": len(prepared["optimized"]),
            "had_history": prepared["had_history"],
            "usage": usage,
            "slm_cost": slm_cost,
            "wall_t": t,
        })
        print("    output_tokens={0}  tool_calls={1}  {2:.1f}s  history={3}".format(
            usage["output_tokens"], usage["tool_calls"], t,
            "yes" if prepared["had_history"] else "no (turn 1)"))
    clear_session(HTTPX_DIR)
    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_chain_results(chain: dict, no_results: list[dict],
                        with_results: list[dict], tool: str) -> None:
    print()
    print("=" * 80)
    print("  Chain {id}: {label}".format(**chain))
    print("  {0}".format(chain["description"]))
    print("=" * 80)
    print()

    cost_fn = claude_cost if tool == "claude-code" else codex_cost
    cost_label = "Claude $" if tool == "claude-code" else "Codex $"

    # Per-turn table
    hdr = "  {:<4} {:<48} {:>7} {:>7} {:>7} {:>7}".format(
        "Turn", "Raw prompt (truncated)", "No-sess", "W/sess", "Δ out", "Δ%")
    print(hdr)
    print("  " + "-" * 82)

    total_no_out   = 0
    total_with_out = 0
    total_no_cost  = 0.0
    total_with_cost = 0.0

    for no, ws in zip(no_results, with_results):
        no_out   = no["usage"]["output_tokens"]
        with_out = ws["usage"]["output_tokens"]
        delta    = with_out - no_out
        pct      = (no_out - with_out) / max(no_out, 1) * 100
        sign     = "+" if delta >= 0 else ""
        pct_sign = "-" if pct >= 0 else "+"
        label    = no["raw"][:46] + (".." if len(no["raw"]) > 46 else "")
        history_marker = "  " if ws["had_history"] else "* "  # * = no history (turn 1)
        print("  {:<4} {}{:<46} {:>7,} {:>7,} {:>7} {:>6.1f}%".format(
            no["turn"],
            history_marker,
            label,
            no_out, with_out,
            "{0}{1:,}".format(sign, delta),
            -pct,
        ))
        total_no_out   += no_out
        total_with_out += with_out
        no_c  = cost_fn(no["usage"])   + no["slm_cost"]
        ws_c  = cost_fn(ws["usage"])   + ws["slm_cost"]
        total_no_cost  += no_c
        total_with_cost += ws_c

    print("  " + "-" * 82)
    total_delta = total_with_out - total_no_out
    total_pct   = (total_no_out - total_with_out) / max(total_no_out, 1) * 100
    sign = "+" if total_delta >= 0 else ""
    print("  {:<4} {:<48} {:>7,} {:>7,} {:>7} {:>6.1f}%".format(
        "TOT", "(all turns)", total_no_out, total_with_out,
        "{0}{1:,}".format(sign, total_delta), -total_pct))
    print()

    # Cost summary
    print("  Cost summary ({0}):".format(cost_label))
    print("  {:<30} {:>10.4f}".format("NO_SESSION total ($)",   total_no_cost))
    print("  {:<30} {:>10.4f}".format("WITH_SESSION total ($)",  total_with_cost))
    net = total_no_cost - total_with_cost
    print("  {0:<30} {1:>+10.4f}  ({2})".format(
        "Net savings ($)", net,
        "WITH_SESSION cheaper" if net > 0 else "NO_SESSION cheaper"))
    print()

    # Verdict
    if total_pct > 10:
        print("  VERDICT: Session history reduced total output tokens by {0:.1f}%".format(total_pct))
        print("           Savings increase across turns as references accumulate.")
    elif total_pct < -10:
        print("  VERDICT: Session history INCREASED total output tokens by {0:.1f}%".format(-total_pct))
        print("           History added noise or expanded scope — investigate.")
    else:
        print("  VERDICT: Negligible difference ({0:.1f}%). Chain was largely self-contained.".format(total_pct))

    # Per-turn savings trend (answers: do savings grow across turns?)
    print()
    print("  Turn-by-turn output token reduction (positive = WITH_SESSION saved):")
    for no, ws in zip(no_results, with_results):
        no_out   = no["usage"]["output_tokens"]
        with_out = ws["usage"]["output_tokens"]
        pct = (no_out - with_out) / max(no_out, 1) * 100
        bar_len = int(abs(pct) / 5)
        bar = ("#" * bar_len) if pct >= 0 else ("." * bar_len)
        direction = "v" if pct >= 0 else "^"
        history_note = "" if ws["had_history"] else " (baseline - no history)"
        print("  T{0}: {1:>+6.1f}%  {2} {3}{4}".format(
            no["turn"], pct, direction, bar, history_note))
    print()


def save_results(chain_id: str, tool: str, no_results: list, with_results: list,
                 out_dir: Path) -> None:
    summary = {
        "chain": chain_id,
        "tool": tool,
        "no_session": no_results,
        "with_session": with_results,
    }
    # Make usage dicts serialisable
    (out_dir / "summary_{0}.json".format(chain_id)).write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Dry-run: just show prepared prompts for each turn
# ---------------------------------------------------------------------------

def dry_run_chain(chain: dict, tool: str) -> None:
    print()
    print("=" * 80)
    print("  Chain {id}: {label}".format(**chain))
    print("=" * 80)

    clear_session(HTTPX_DIR)
    reset_repo(HTTPX_DIR)
    for i, raw in enumerate(chain["turns"], 1):
        print("\n  Turn {0}: {1}".format(i, raw))
        print("  " + "-" * 60)

        no_prep  = prepare_no_session_turn(raw, HTTPX_DIR, tool)
        with_prep = prepare_with_session_turn(raw, HTTPX_DIR, tool)
        record_to_session(HTTPX_DIR, raw, with_prep["rewrite"])

        print("  NO_SESSION   intent={intent} scope={scope}  {chars} chars".format(
            chars=len(no_prep["optimized"]), **no_prep))
        print("  WITH_SESSION intent={intent} scope={scope}  {chars} chars  history={had}".format(
            chars=len(with_prep["optimized"]), had=with_prep["had_history"], **with_prep))

        # Show diff in grounded prompts (first 250 chars each)
        g_no   = no_prep["grounded"][:250].replace("\n", " ")
        g_with = with_prep["grounded"][:250].replace("\n", " ")
        if g_no != g_with:
            print("  NO_SESSION   grounded: {0}...".format(g_no))
            print("  WITH_SESSION grounded: {0}...".format(g_with))

    clear_session(HTTPX_DIR)
    reset_repo(HTTPX_DIR)


# ---------------------------------------------------------------------------
# Reprint from saved results
# ---------------------------------------------------------------------------

def reprint_chain(chain: dict, tool: str, out_dir: Path) -> None:
    summary_path = out_dir / tool / "summary_{0}.json".format(chain["id"])
    if not summary_path.exists():
        print("  [skip] no saved results for {0}/{1}".format(chain["id"], tool))
        return
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    print_chain_results(chain, data["no_session"], data["with_session"], tool)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", default="all",
                        choices=["1", "2", "3", "all"],
                        help="Which chain to run (default: all)")
    parser.add_argument("--tool", default="all",
                        choices=["codex", "claude-code", "all"],
                        help="Downstream tool (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prepared prompts without running the tool")
    parser.add_argument("--reprint", action="store_true",
                        help="Re-display saved results without re-running")
    args = parser.parse_args()

    targets = CHAINS if args.chain == "all" else [
        c for c in CHAINS if c["id"] == "chain{0}".format(args.chain)
    ]
    tools = ["codex", "claude-code"] if args.tool == "all" else [args.tool]

    for chain in targets:
        if args.dry_run:
            dry_run_chain(chain, tools[0])
            continue

        for tool in tools:
            if args.reprint:
                reprint_chain(chain, tool, OUT_DIR)
                continue

            tool_dir = OUT_DIR / tool / chain["id"]
            tool_dir.mkdir(parents=True, exist_ok=True)

            print("\n" + "=" * 60)
            print("Running {chain} with {tool}".format(chain=chain["id"], tool=tool))

            print("\n  --- NO_SESSION runs ---")
            reset_repo(HTTPX_DIR)
            no_results = run_chain_no_session(chain, tool, tool_dir)

            print("\n  --- WITH_SESSION runs ---")
            reset_repo(HTTPX_DIR)
            with_results = run_chain_with_session(chain, tool, tool_dir)

            reset_repo(HTTPX_DIR)

            save_results(chain["id"], tool, no_results, with_results, OUT_DIR / tool)
            print_chain_results(chain, no_results, with_results, tool)

    print("\nOutput files:", OUT_DIR)


if __name__ == "__main__":
    main()
