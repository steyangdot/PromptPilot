"""Read JSONL run log and compute cumulative token/cost stats."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from prpt.ui import _style


DEFAULT_COMPRESS_STATS_PATH = Path(os.environ.get(
    "PROMPTPILOT_COMPRESS_STATS",
    str(Path.home() / ".promptpilot" / "compress_stats.jsonl"),
))


def load_runs(log_path: str) -> List[dict]:
    path = Path(log_path)
    if not path.exists():
        return []
    runs: List[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def load_compress_stats(path: Optional[Path] = None) -> List[dict]:
    """Read the PostToolUse compression hook telemetry log.

    Records are written one per Bash tool call seen by the hook. See
    `.codex/hooks/compress_tool_output.py` / `.claude/hooks/compress_tool_output.py`
    for the writer.
    """
    p = Path(path) if path else DEFAULT_COMPRESS_STATS_PATH
    if not p.exists():
        return []
    records: List[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def print_compress_stats(
    records: List[dict], theme: str = "plain", path: Optional[Path] = None,
) -> None:
    """Aggregate and print compression telemetry."""
    if not records:
        print(_style("  --- Tool-output compression ---", "dim", theme))
        print("  No compression events logged yet.")
        print("  Install the PostToolUse hook to start capturing -- see README "
              "'Tool-output compression' section.")
        print()
        return

    total = len(records)
    applied = [r for r in records if r.get("applied")]
    n_applied = len(applied)
    n_skipped = total - n_applied

    total_orig = sum(r.get("original_len", 0) for r in records)
    total_compressed = sum(r.get("compressed_len", 0) for r in records)
    bytes_saved = total_orig - total_compressed
    overall_pct = (100 * bytes_saved / total_orig) if total_orig > 0 else 0

    by_kind: dict = defaultdict(lambda: {"count": 0, "applied": 0, "orig": 0, "compressed": 0})
    for r in records:
        k = by_kind[r.get("cmd_kind", "unknown")]
        k["count"] += 1
        if r.get("applied"):
            k["applied"] += 1
        k["orig"] += r.get("original_len", 0)
        k["compressed"] += r.get("compressed_len", 0)

    print(_style("  --- Tool-output compression ---", "title", theme))
    print("  Total events:         {0:,}  ({1:,} compressed, {2:,} passed through)"
          .format(total, n_applied, n_skipped))
    print("  Original bytes:       {0:>12,}".format(total_orig))
    print("  Compressed bytes:     {0:>12,}".format(total_compressed))
    print("  Eligible bytes saved: {0:>12,}  ({1:+.1f}%)".format(
        bytes_saved, overall_pct))
    print("  Note: 'eligible' = bytes the compressor removed from its output;")
    print("  realized in-context only if the CLI applied updatedToolOutput")
    print("  (claude >= v2.1.120 and the PostToolUse hook actually fired).")
    print()
    print("  By command type:")
    print("    {0:<14}{1:>8}{2:>10}{3:>14}{4:>10}".format(
        "kind", "events", "applied", "bytes saved", "saved %"))
    for kind, k in sorted(by_kind.items(), key=lambda kv: -(kv[1]["orig"] - kv[1]["compressed"])):
        saved = k["orig"] - k["compressed"]
        pct = (100 * saved / k["orig"]) if k["orig"] > 0 else 0
        print("    {0:<14}{1:>8}{2:>10}{3:>14,}{4:>9.1f}%".format(
            kind, k["count"], k["applied"], saved, pct))
    print()
    print("  Log file: {0}".format(path or DEFAULT_COMPRESS_STATS_PATH))
    print()


def print_stats(log_path: str, last_n: Optional[int] = None, theme: str = "plain") -> None:
    runs = load_runs(log_path)
    if not runs:
        print("No runs logged yet.")
        print("Use --log-runs to start recording: prpt --log-runs --normalizer slm ...")
        # Compression telemetry is collected by the PostToolUse hooks (separate
        # log) so we still surface it when no `--log-runs` runs exist.
        compress_records = load_compress_stats()
        if compress_records:
            print()
            print_compress_stats(compress_records, theme=theme)
        return

    # Filter to runs that have token_stats
    with_stats = [r for r in runs if r.get("token_stats")]
    total_runs = len(runs)

    if last_n:
        runs = runs[-last_n:]
        with_stats = [r for r in runs if r.get("token_stats")]

    # Aggregates
    total_original_tokens = 0
    total_final_tokens = 0
    total_slm_input = 0
    total_slm_output = 0
    total_slm_cost = 0.0
    total_gross_savings = 0.0
    total_net_savings = 0.0
    total_actual_input = 0
    total_actual_output = 0
    total_actual_cost = 0.0
    runs_with_actual = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cache_savings = 0.0
    normalizer_counts: dict = {}
    tool_counts: dict = {}

    for run in runs:
        norm = run.get("normalizer", "unknown")
        normalizer_counts[norm] = normalizer_counts.get(norm, 0) + 1
        tool = run.get("tool", "unknown")
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    for run in with_stats:
        ts = run["token_stats"]
        total_original_tokens += ts.get("original_tokens", 0)
        total_final_tokens += ts.get("final_tokens", 0)
        total_slm_input += ts.get("haiku_input_tokens", 0)
        total_slm_output += ts.get("haiku_output_tokens", 0)
        total_slm_cost += ts.get("haiku_cost_usd", 0.0)
        total_gross_savings += ts.get("gross_savings_usd", 0.0)
        total_net_savings += ts.get("net_savings_usd", 0.0)
        if ts.get("actual_input_tokens") is not None:
            runs_with_actual += 1
            total_actual_input += ts["actual_input_tokens"]
            total_actual_output += ts["actual_output_tokens"]
            total_actual_cost += ts.get("actual_total_cost_usd", 0.0)
        total_cache_read += ts.get("cache_read_input_tokens", 0) or 0
        total_cache_write += ts.get("cache_creation_input_tokens", 0) or 0
        total_cache_savings += ts.get("cache_savings_usd", 0.0) or 0.0

    # Time range
    timestamps = []
    for r in runs:
        t = r.get("timestamp_utc")
        if t:
            try:
                timestamps.append(datetime.fromisoformat(t))
            except ValueError:
                pass

    # Print
    scope = "last {0} runs".format(last_n) if last_n else "all time"
    print(_style("=== PROMPTPILOT STATS ({0}) ===".format(scope), "title", theme))
    print()

    print("  Runs logged:          {0}".format(len(runs)))
    print("  Runs with token data: {0}".format(len(with_stats)))
    if timestamps:
        print("  Date range:           {0} .. {1}".format(
            timestamps[0].strftime("%Y-%m-%d %H:%M"),
            timestamps[-1].strftime("%Y-%m-%d %H:%M"),
        ))
    print()

    print("  Normalizers used:")
    for norm, count in sorted(normalizer_counts.items(), key=lambda kv: -kv[1]):
        print("    {0:<20} {1} runs".format(norm, count))
    print()

    print("  Tools used:")
    for tool, count in sorted(tool_counts.items(), key=lambda kv: -kv[1]):
        print("    {0:<20} {1} runs".format(tool, count))
    print()

    if not with_stats:
        print("  No token stats recorded. Use --normalizer slm for token tracking.")
        return

    delta = total_original_tokens - total_final_tokens
    delta_pct = (delta / total_original_tokens * 100) if total_original_tokens > 0 else 0

    print("  --- Prompt token savings (estimated) ---")
    print("  Original tokens:      {0:>12,}".format(total_original_tokens))
    print("  Final tokens:         {0:>12,}".format(total_final_tokens))
    print("  Delta:                {0:>12,}  ({1:+.1f}%)".format(delta, -delta_pct))
    print()
    print("  SLM tokens consumed:  {0:,} in + {1:,} out".format(total_slm_input, total_slm_output))
    print("  SLM total cost:       ${0:,.6f}".format(total_slm_cost))
    print("  Gross savings:        ${0:,.6f}".format(total_gross_savings))
    verdict = "SAVED" if total_net_savings > 0 else "COST"
    print("  Net savings:          ${0:,.6f}  ({1})".format(abs(total_net_savings), verdict))
    print()

    if runs_with_actual:
        print("  --- Actual downstream usage ({0} runs) ---".format(runs_with_actual))
        print("  Actual input tokens:  {0:>12,}".format(total_actual_input))
        print("  Actual output tokens: {0:>12,}".format(total_actual_output))
        print("  Actual total cost:    ${0:,.6f}".format(total_actual_cost))
        print()

    if total_cache_read or total_cache_write:
        print("  --- Anthropic prompt caching ---")
        print("  Cache reads:          {0:>12,} tokens".format(total_cache_read))
        print("  Cache writes:         {0:>12,} tokens".format(total_cache_write))
        hit_ratio = (
            total_cache_read / (total_cache_read + total_cache_write)
            if (total_cache_read + total_cache_write) > 0 else 0.0
        )
        print("  Cache hit ratio:      {0:>12.1%}".format(hit_ratio))
        print("  Cache savings:        ${0:,.6f}".format(total_cache_savings))
        print()

    print("  Log file: {0}".format(log_path))
    print()

    # Compression telemetry — separate log, written by the PostToolUse hooks.
    compress_records = load_compress_stats()
    print_compress_stats(compress_records, theme=theme)
