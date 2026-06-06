"""Terminal display helpers for review output and token stats."""
from __future__ import annotations

import argparse
import textwrap
from typing import Optional

from prpt.core.constants import MODEL_PRICING, DEFAULT_TARGET_MODEL
from prpt.core.types import NormalizedRequest, TokenStats, ValidationResult


ANSI_RESET = "\033[0m"
THEMES = {
    "plain": {},
    "dark": {
        "title": "\033[1;97m",
        "heading": "\033[1;96m",
        "label": "\033[38;5;153m",
        "value": "\033[38;5;230m",
        "muted": "\033[38;5;245m",
        "accent": "\033[38;5;121m",
    },
}


def _style(text: str, role: str, theme: str = "plain") -> str:
    color = THEMES.get(theme, {}).get(role)
    if not color:
        return text
    return "{0}{1}{2}".format(color, text, ANSI_RESET)


def _print_kv(label: str, value: object, theme: str) -> None:
    print("{0}: {1}".format(_style(label, "label", theme), _style(str(value), "value", theme)))


def print_preview(
    *,
    route: str,
    task_type: str,
    confidence: str,
    spec_dict: Optional[dict],
    rewrite_text: str,
    theme: str = "plain",
) -> None:
    """Render one interactive-preview turn: the routing line, the v2 JSON
    ExecutionSpec, and the rewritten prompt (or clarifying question)."""
    import json

    print()
    print("{0}   {1}   {2}".format(
        _style("route=" + route, "accent", theme),
        _style("task=" + task_type, "muted", theme),
        _style("confidence=" + confidence, "muted", theme),
    ))
    if spec_dict is not None:
        print(_style("ExecutionSpec (JSON):", "heading", theme))
        print(json.dumps(spec_dict, indent=2, ensure_ascii=False))
    else:
        print(_style("(v1 normalizer — no JSON ExecutionSpec for this backend)", "muted", theme))
    label = "Clarifying question:" if route == "clarify" else "Rewritten prompt → coding agent:"
    print(_style(label, "heading", theme))
    print(rewrite_text)
    print()


def print_review(
    normalized: NormalizedRequest,
    validation: ValidationResult,
    theme: str = "plain",
) -> None:
    _print_kv("Detected task type", normalized.task_type, theme)
    _print_kv("Confidence", normalized.confidence, theme)
    _print_kv("Normalizer rewrite mode", normalized.rewrite_mode, theme)
    print()
    print(_style("Hard constraints:", "heading", theme))
    for item in normalized.hard_constraints or ["None detected"]:
        print("{0} {1}".format(_style("-", "muted", theme), item))
    print()
    print(_style("Ambiguities:", "heading", theme))
    for item in normalized.ambiguities or ["None detected"]:
        print("{0} {1}".format(_style("-", "muted", theme), item))
    print()
    print(_style("Assumptions:", "heading", theme))
    for item in normalized.assumptions or ["None"]:
        print("{0} {1}".format(_style("-", "muted", theme), item))
    print()
    print(_style("Semantic validation:", "heading", theme))
    print("{0} status: {1}".format(
        _style("-", "muted", theme),
        _style(validation.semantic_preservation, "accent", theme),
    ))
    for issue in validation.issues:
        print("{0} issue: {1}".format(_style("-", "muted", theme), issue))
    print()


def print_token_stats(stats: TokenStats, theme: str = "plain") -> None:
    delta_sign = "-" if stats.delta_tokens >= 0 else "+"
    print(_style("=== TOKEN STATS ===", "title", theme))

    if stats.actual_input_tokens is not None:
        # Full before/after — actual numbers from the API call
        raw_in_cost = stats.original_tokens * MODEL_PRICING.get(
            stats.target_model, {"input": 15.00}
        )["input"] / 1_000_000
        in_delta = stats.original_tokens - stats.actual_input_tokens
        in_delta_sign = "-" if in_delta >= 0 else "+"
        print("  {:<14} {:>8}  {:>8}  {:>12}".format("", "INPUT", "OUTPUT", "COST (USD)"))
        print("  {:<14} {:>8}  {:>8}  {:>12}".format(
            "Before (raw)", stats.original_tokens, "---",
            "${:.6f}".format(raw_in_cost),
        ))
        print("  {:<14} {:>8}  {:>8}  {:>12}".format(
            "After (actual)", stats.actual_input_tokens, stats.actual_output_tokens,
            "${:.6f}".format(stats.actual_total_cost_usd or 0.0),
        ))
        print("  {:<14} {:>8}  {:>8}  {:>12}".format(
            "SLM cost",
            "{0}in+{1}out".format(stats.haiku_input_tokens, stats.haiku_output_tokens),
            "", "${:.6f}".format(stats.haiku_cost_usd),
        ))
        print("  " + "-" * 52)
        input_saved = raw_in_cost - (stats.actual_total_cost_usd or 0.0)
        net = input_saved - stats.haiku_cost_usd
        print("  Input delta:  {0}{1} tokens  (counted -> actual)".format(in_delta_sign, abs(in_delta)))
        print("  Net savings:  ${:.6f}  ({})".format(abs(net), "SAVES" if net > 0 else "COSTS"))
    else:
        # Estimated only (no actual API call made)
        print("  Prompt tokens (original -> final): {0} -> {1}  ({2}{3} tokens, counted)".format(
            stats.original_tokens, stats.final_tokens,
            delta_sign, abs(stats.delta_tokens),
        ))
        print("  SLM call:     {0} in + {1} out = ${2:.6f}".format(
            stats.haiku_input_tokens, stats.haiku_output_tokens, stats.haiku_cost_usd,
        ))
        print("  Gross savings on {0}: ${1:.6f}".format(stats.target_model, stats.gross_savings_usd))
        verdict = "SAVES" if stats.net_savings_usd > 0 else "COSTS"
        print("  Net ({0}):       ${1:.6f}".format(verdict, abs(stats.net_savings_usd)))
        print("  (Use --tool anthropic or --tool openai to see actual before/after)")
    print()


def _truncate(text: str, max_lines: int = 12) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n... ({0} more lines)".format(len(lines) - max_lines)


def print_compare(
    raw_prompt: str,
    final_prompt: str,
    normalized: NormalizedRequest,
    token_stats: Optional[TokenStats],
    args: argparse.Namespace,
) -> None:
    theme = getattr(args, "theme", "plain")
    target_model = getattr(args, "target_model", DEFAULT_TARGET_MODEL)
    normalizer_name = getattr(args, "normalizer", "heuristic")
    target_price = MODEL_PRICING.get(target_model, {"input": 15.00, "output": 75.00})

    raw_chars = len(raw_prompt)
    opt_chars = len(final_prompt)

    print(_style("=" * 60, "title", theme))
    print(_style("  COMPARE: raw prompt vs optimized", "title", theme))
    print("  {0}: {1}  |  {2}: {3}".format(
        _style("normalizer", "label", theme), normalizer_name,
        _style("target", "label", theme), target_model,
    ))
    print(_style("=" * 60, "title", theme))
    print()

    # Side-by-side prompts (truncated for readability)
    print(_style("--- RAW PROMPT ({0} chars) ---".format(raw_chars), "heading", theme))
    print(_truncate(raw_prompt))
    print()

    if normalized.normalized_prompt != raw_prompt:
        print(_style(
            "--- REWRITTEN PROMPT ({0} chars) ---".format(len(normalized.normalized_prompt)),
            "heading",
            theme,
        ))
        print(_truncate(normalized.normalized_prompt))
        print()

    print(_style("--- FINAL DOWNSTREAM PROMPT ({0} chars) ---".format(opt_chars), "heading", theme))
    print(_truncate(final_prompt))
    print()

    # Token comparison
    print(_style("=" * 60, "title", theme))
    print(_style("  TOKEN COMPARISON", "title", theme))
    print(_style("=" * 60, "title", theme))

    if token_stats:
        raw_tok = token_stats.original_tokens
        opt_tok = token_stats.final_tokens
        delta = raw_tok - opt_tok
        pct = (delta / raw_tok * 100) if raw_tok > 0 else 0
        raw_cost = raw_tok * target_price["input"] / 1_000_000
        opt_cost = opt_tok * target_price["input"] / 1_000_000

        print()
        print(_style("  Input tokens", "heading", theme))
        print("  {:<28} {:>10} {:>10} {:>10}".format("", "RAW", "OPTIMIZED", "DELTA"))
        print("  " + "-" * 58)
        print("  {:<28} {:>10,} {:>10,} {:>+10,}".format(
            "Input tokens (counted)", raw_tok, opt_tok, -delta))
        print("  {:<28} {:>10} {:>10} {:>10}".format(
            "Input cost ({0})".format(target_model[:20]),
            "${:.6f}".format(raw_cost),
            "${:.6f}".format(opt_cost),
            "${:+.6f}".format(opt_cost - raw_cost),
        ))
        print("  {:<28} {:>10} {:>10}".format(
            "SLM rewrite cost", "", "${:.6f}".format(token_stats.haiku_cost_usd)))

        # Cost summary
        input_delta_cost = opt_cost - raw_cost  # positive = costs more
        total_input_cost = input_delta_cost + token_stats.haiku_cost_usd

        print()
        print(_style("  Cost summary", "heading", theme))
        print("  " + "-" * 58)
        print("  {:<28} {:>10} {:>10} {:>10}".format(
            "Input cost change", "", "", "${:+.6f}".format(input_delta_cost)))
        print("  {:<28} {:>10} {:>10} {:>10}".format(
            "SLM rewrite cost", "", "", "${:+.6f}".format(token_stats.haiku_cost_usd)))
        print("  " + "-" * 58)
        print("  {:<28} {:>10} {:>10} {:>10}".format(
            "Total added cost", "", "", "${:+.6f}".format(total_input_cost)))
        print()
        print("  Value: grounded prompt with real file paths and constraints")
        print("  targets the right code on the first try, reducing follow-up")
        print("  rounds and tool calls in agentic workflows.")
        print("  (Use --tool anthropic/openai to measure actual output tokens)")
        print()
    else:
        # Heuristic path — no SLM token counting, estimate from chars
        raw_est = raw_chars // 4
        opt_est = opt_chars // 4
        delta_est = raw_est - opt_est
        print()
        print("  {:<24} {:>10} {:>10} {:>10}".format("", "RAW", "OPTIMIZED", "DELTA"))
        print("  " + "-" * 54)
        print("  {:<24} {:>10,} {:>10,} {:>+10,}".format(
            "Chars", raw_chars, opt_chars, opt_chars - raw_chars))
        print("  {:<24} {:>10,} {:>10,} {:>+10,}".format(
            "Tokens (est. chars/4)", raw_est, opt_est, -delta_est))
        raw_cost = raw_est * target_price["input"] / 1_000_000
        opt_cost = opt_est * target_price["input"] / 1_000_000
        print("  {:<24} {:>10} {:>10} {:>10}".format(
            "Input cost (est.)",
            "${:.6f}".format(raw_cost),
            "${:.6f}".format(opt_cost),
            "${:+.6f}".format(opt_cost - raw_cost),
        ))
        print()
        print("  (Use --normalizer slm for accurate token counts)")
        print()
