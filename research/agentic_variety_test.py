"""
Prompt variety test: compare 3 variants through Codex
  RAW       — unmodified prompt
  GROUNDED  — SLM-rewritten + repo context
  OPTIMIZED — GROUNDED + output format constraints (scope pinning, length budget)

Focus: output token reduction from the new optimizations.

Usage:
    python agentic_variety_test.py [--prompt-idx 0|1|2|all]
    python agentic_variety_test.py --reprint          # re-display from saved JSONL
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from prpt.normalizers.base import build_final_downstream_prompt, build_output_suffix, create_normalizer
from prpt.repo.collector import RepoContextCollector

HTTPX_DIR = "C:/projects/httpx"
OUT_DIR = Path(__file__).parent / "agentic_variety"

# Supported downstream tools
TOOLS = ("codex", "claude-code")

PROMPTS = [
    {
        "id": "bugfix",
        "label": "Bug fix (pinpoint)",
        "raw": "fix the timeout not being passed through to the underlying socket in the sync client",
    },
    {
        "id": "refactor",
        "label": "Refactor (localized)",
        "raw": "refactor the connection pool to support async context managers",
    },
    {
        "id": "feature",
        "label": "Feature addition (localized)",
        "raw": "add retry-after header support to the retry logic",
    },
    {
        "id": "explain",
        "label": "Explanation (explain-intent)",
        "raw": "explain how redirects are handled",
    },
]


# ---------------------------------------------------------------------------
# Prompt preparation
# ---------------------------------------------------------------------------

def prepare_prompts(raw: str, cwd: str, tool: str = "codex") -> dict:
    # Map tool name to the key used in build_output_suffix
    suffix_tool = "anthropic" if tool == "claude-code" else tool
    """
    Returns dict with keys: raw, grounded, optimized, scope, intent, suffix.
    """
    repo = RepoContextCollector().collect(cwd)
    normalizer = create_normalizer("slm", load_repo_content=True)
    normalized = normalizer.normalize(raw, repo)
    grounded = build_final_downstream_prompt(normalized, repo)

    intent = getattr(normalizer, "_last_intent", "act")
    scope = getattr(normalizer, "_last_scope", "localized")
    suffix = build_output_suffix(scope, suffix_tool) if intent == "act" else ""
    optimized = grounded + "\n\n" + suffix if suffix else grounded

    return {
        "raw": raw,
        "grounded": grounded,
        "optimized": optimized,
        "intent": intent,
        "scope": scope,
        "suffix": suffix,
    }


# ---------------------------------------------------------------------------
# Codex runner
# ---------------------------------------------------------------------------

CODEX_TIMEOUT_SEC = 300
# CLAUDE_TIMEOUT_SEC: default raised 600 -> 1200 (2026-05-16) after diagnostic
# showed chain1 T1 hits the 600s cap on sonnet across every run we observed.
# With opus now the default model and known to be slower per tool call, 600s
# is definitely insufficient for first-turn cold-exploration bug-fix prompts.
# Override per-run via env var. Originally calibrated to chain4 5-turn cases.
CLAUDE_TIMEOUT_SEC = int(os.environ.get("CLAUDE_TIMEOUT_SEC", "1200"))


# Backward-compat re-export. Centralized in prpt/_subprocess.py so that
# new launchers and scripts that import from either location get the same
# implementation. See prpt/_subprocess.py for details on why this exists
# (Windows process-handle exhaustion when zombies accumulate).
from prpt._subprocess import reap_claude_orphans  # noqa: E402, F401


def run_codex(prompt: str, out_jsonl: Path, cwd: str) -> tuple[float, int]:
    codex = shutil.which("codex") or shutil.which("codex.cmd") or "codex"
    cmd = [codex, "exec", "--dangerously-bypass-approvals-and-sandbox",
           "--skip-git-repo-check", "--cd", cwd, "--json", "-"]
    t0 = time.time()
    with open(out_jsonl, "w", encoding="utf-8") as fout:
        try:
            proc = subprocess.run(
                cmd, input=prompt, text=True, encoding="utf-8",
                stdout=fout, cwd=cwd, timeout=CODEX_TIMEOUT_SEC,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = 124
    return time.time() - t0, rc


def run_claude_code(prompt: str, out_json: Path, cwd: str, model: str | None = None,
                     session_id: str | None = None) -> tuple[float, int]:
    """Run Claude Code -p --output-format json with prompt on stdin.

    When USE_BUILTIN_SESSION=1 env var is set, built-in session persistence is
    enabled and `--resume <session_id>` is passed when a session_id is provided.
    """
    claude = shutil.which("claude") or shutil.which("claude.cmd") or "claude"
    # Default changed sonnet -> opus 2026-05-16. Opus is materially slower per
    # tool call but produces more reliable file changes on chain1 / chain4
    # bug-fix prompts. Override per-run with CLAUDE_MODEL=sonnet env var.
    # Reminder: opus will likely need CLAUDE_TIMEOUT_SEC >= 1200 on chain1 T1
    # (sonnet already hits the 600s cap there; opus is slower).
    resolved_model = model or os.environ.get("CLAUDE_MODEL", "opus")
    use_builtin = os.environ.get("USE_BUILTIN_SESSION") == "1"
    # Permission strategy (FIX_PLAN P2 #5):
    #   default: --dangerously-skip-permissions (legacy behavior, fastest setup)
    #   opt-out: USE_PERMISSION_ALLOWLIST=1 -- relies on the target repo's
    #     .claude/settings.json instead. Safer and more durable against future
    #     claude-code releases that may deprecate the dangerous flag, BUT will
    #     hang the run if the allowlist is missing a tool the agent needs.
    use_allowlist = os.environ.get("USE_PERMISSION_ALLOWLIST") == "1"
    # Auth strategy (default flipped 2026-05-16: was opt-in Max, now opt-out):
    #   default: hybrid pattern -- the parent process uses ANTHROPIC_API_KEY /
    #     OPENAI_API_KEY for the SLM normalizer (cheap, fast SDK), and the
    #     subprocess uses Max OAuth (no --bare, ANTHROPIC_API_KEY scrubbed
    #     from subprocess env). Requires `claude auth login --claudeai`
    #     completed once. This is the cost-killer pattern: zero Anthropic API
    #     spend on the downstream agent; quota charged against Max sub.
    #   opt-out: USE_API_KEY_AUTH=1 reverts to the old conservative behavior
    #     -- adds --bare flag and lets ANTHROPIC_API_KEY through to the
    #     subprocess. Use this for sustained chain experiments where the
    #     compliance-conservative posture under the ToS "ordinary use"
    #     framing matters more than cost (e.g. nightly N>=10 batches).
    #
    # Note: the default Max path invokes the official `claude` binary which
    # uses its own OAuth session -- we never touch the token. That's a
    # different pattern from the OpenClaw/OpenCode tools enforced against on
    # Apr 4 2026 (those extracted tokens and made direct API calls). See
    # memory/compliance_vs_openclaw.md and README.md compliance section.
    use_api_key_auth = os.environ.get("USE_API_KEY_AUTH") == "1"
    # Back-compat: respect legacy USE_MAX_AUTH=0 as an opt-out signal too,
    # so anyone with that env var set explicitly to "0" still gets API mode.
    if os.environ.get("USE_MAX_AUTH") == "0":
        use_api_key_auth = True
    use_max_auth = not use_api_key_auth
    cmd = [
        claude, "-p",
        "--output-format", "json",
        "--model", resolved_model,
    ]
    if not use_max_auth:
        cmd.append("--bare")
    if not use_allowlist:
        cmd.append("--dangerously-skip-permissions")
    if not use_builtin:
        cmd.append("--no-session-persistence")
    elif session_id:
        cmd.extend(["--resume", session_id])
    subprocess_env = None
    if use_max_auth:
        subprocess_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    t0 = time.time()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Write stdout straight to the target file and discard stderr at the OS
        # level. This avoids capture_output=True's reader threads entirely — on
        # Python 3.11 Windows they were crashing with UnicodeDecodeError('gbk')
        # even in binary mode, which blocked subprocess.run from returning and
        # defeated the timeout. Fd-level redirection means no Python IO wrapping.
        with open(out_json, "wb") as fout:
            proc = subprocess.run(
                cmd, input=prompt.encode("utf-8"),
                stdout=fout, stderr=subprocess.DEVNULL,
                cwd=cwd, timeout=CLAUDE_TIMEOUT_SEC,
                env=subprocess_env,
            )
        if out_json.stat().st_size == 0:
            out_json.write_text("{}", encoding="utf-8")
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        # subprocess.run kills the direct child on timeout, but grandchildren
        # on Windows may survive and accumulate -- enough zombies (~10+) can
        # exhaust Windows process handles and crash the whole system. Reap
        # immediately so they don't pile up across turns. Write an empty
        # result so downstream scoring marks this turn as a bail/failure.
        out_json.write_text("{}", encoding="utf-8")
        rc = 124
        try:
            reap_claude_orphans()
        except Exception:
            pass
    return time.time() - t0, rc


def parse_usage_claude(json_path: Path) -> dict:
    """Parse token usage from Claude Code --output-format json result."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                "tool_calls": 0, "agent_messages": 0, "events": 0}
    usage = data.get("usage", {})
    # input_tokens in Claude Code JSON = uncached new input
    # cache_read_input_tokens = tokens served from cache
    # cache_creation_input_tokens = tokens written to cache (billed as input)
    input_tok = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    cached_tok = usage.get("cache_read_input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    num_turns = data.get("num_turns", 0)
    return {
        "input_tokens": input_tok + cached_tok,   # total input (same basis as Codex)
        "cached_tokens": cached_tok,
        "output_tokens": output_tok,
        "tool_calls": num_turns,
        "agent_messages": num_turns,
        "events": num_turns,
        "total_cost_usd": data.get("total_cost_usd", 0.0),
    }


def parse_usage(jsonl_path: Path) -> dict:
    events = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    usage = {}
    tool_calls = 0
    agent_messages = 0
    for e in events:
        if e.get("type") == "turn.completed":
            usage = e.get("usage", {})
        if e.get("type") == "item.completed":
            item = e.get("item", {})
            if item.get("type") == "command_execution":
                tool_calls += 1
            if item.get("type") == "agent_message":
                agent_messages += 1

    return {
        "input_tokens": usage.get("input_tokens", 0),
        "cached_tokens": usage.get("cached_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "tool_calls": tool_calls,
        "agent_messages": agent_messages,
        "events": len(events),
    }


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------

def codex_cost(u: dict) -> float:
    """o4-mini pricing: $1.10/M input, $4.40/M output (cached: $0.275/M)."""
    uncached_in = u["input_tokens"] - u["cached_tokens"]
    return (
        uncached_in * 1.10 / 1_000_000
        + u["cached_tokens"] * 0.275 / 1_000_000
        + u["output_tokens"] * 4.40 / 1_000_000
    )


def claude_cost(u: dict) -> float:
    """Use total_cost_usd if available (reported by Claude Code directly)."""
    if u.get("total_cost_usd"):
        return u["total_cost_usd"]
    # Fallback: Sonnet pricing $3/M input, $15/M output, $0.30/M cached
    uncached_in = u["input_tokens"] - u["cached_tokens"]
    return (
        uncached_in * 3.00 / 1_000_000
        + u["cached_tokens"] * 0.30 / 1_000_000
        + u["output_tokens"] * 15.00 / 1_000_000
    )


def tool_cost(u: dict, tool: str) -> float:
    if tool.get("total_cost_usd"):
        return u["total_cost_usd"]
    return claude_cost(u) if tool == "claude-code" else codex_cost(u)


def slm_cost_estimate(raw: str, grounded: str) -> float:
    """Rough Haiku cost: input = raw + ~5K context, output = grounded."""
    input_tok = len(raw) // 4 + 5000
    output_tok = len(grounded) // 4
    return input_tok * 0.80 / 1_000_000 + output_tok * 4.00 / 1_000_000


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_result(label: str, prompts: dict,
                 raw_u: dict, grnd_u: dict, opt_u: dict,
                 raw_t: float, grnd_t: float, opt_t: float,
                 tool: str = "codex") -> None:
    raw = prompts["raw"]
    grounded = prompts["grounded"]
    optimized = prompts["optimized"]
    scope = prompts["scope"]
    intent = prompts["intent"]
    suffix = prompts["suffix"]

    slm = slm_cost_estimate(raw, grounded)
    raw_cost = _cost(raw_u, tool)
    grnd_cost = _cost(grnd_u, tool)
    opt_cost = _cost(opt_u, tool)

    print()
    print("=" * 72)
    print("  {0}".format(label))
    print("  intent={0}  scope={1}  constraints={2}".format(
        intent, scope, "yes" if suffix else "no (explain/broad/new)"))
    print("=" * 72)

    if suffix:
        print("  Output constraint appended:")
        for line in suffix.splitlines():
            print("    " + line)
        print()

    print("  Prompt sizes (chars):  raw={0}  grounded={1}  optimized={2}".format(
        len(raw), len(grounded), len(optimized)))
    print()

    hdr = "  {:<22} {:>11} {:>11} {:>11} {:>9} {:>9}".format(
        "Metric", "RAW", "GROUNDED", "OPTIMIZED", "Δ grnd", "Δ opt")
    print(hdr)
    print("  " + "-" * 70)

    def row(name, a, b, c, fmt="{:,}"):
        db = b - a
        dc = c - a
        sign_b = "+" if db >= 0 else ""
        sign_c = "+" if dc >= 0 else ""
        print("  {:<22} {:>11,} {:>11,} {:>11,} {:>9} {:>9}".format(
            name, a, b, c,
            "{0}{1:,}".format(sign_b, db),
            "{0}{1:,}".format(sign_c, dc),
        ))

    row("Input tokens",   raw_u["input_tokens"],  grnd_u["input_tokens"],  opt_u["input_tokens"])
    row("Cached tokens",  raw_u["cached_tokens"], grnd_u["cached_tokens"], opt_u["cached_tokens"])
    row("Output tokens",  raw_u["output_tokens"], grnd_u["output_tokens"], opt_u["output_tokens"])
    row("Total tokens",
        raw_u["input_tokens"]  + raw_u["output_tokens"],
        grnd_u["input_tokens"] + grnd_u["output_tokens"],
        opt_u["input_tokens"]  + opt_u["output_tokens"])
    row("Tool calls",     raw_u["tool_calls"],    grnd_u["tool_calls"],    opt_u["tool_calls"])
    row("Agent messages", raw_u["agent_messages"], grnd_u["agent_messages"], opt_u["agent_messages"])

    print("  " + "-" * 70)

    def cost_row(name, a, b, c):
        db = b - a
        dc = c - a
        sign_b = "+" if db >= 0 else ""
        sign_c = "+" if dc >= 0 else ""
        print("  {:<22} {:>11.4f} {:>11.4f} {:>11.4f} {:>9} {:>9}".format(
            name, a, b, c,
            "{0}{1:.4f}".format(sign_b, db),
            "{0}{1:.4f}".format(sign_c, dc),
        ))

    cost_label = "Claude cost ($)" if tool == "claude-code" else "Codex cost ($)"
    cost_row(cost_label, raw_cost, grnd_cost, opt_cost)
    print("  {:<22} {:>11} {:>11.4f}".format("SLM cost ($)", "", slm))

    net_grnd = raw_cost - grnd_cost - slm
    net_opt  = raw_cost - opt_cost  - slm
    print("  {:<22} {:>11} {:>11} {:>11.4f} {:>9} {:>9}".format(
        "Net savings ($)", "",
        "{:+.4f}".format(-net_grnd),
        net_opt,
        "",
        "{:+.4f}".format(net_opt - net_grnd),
    ))
    print("  {:<22} {:>10.1f}s {:>10.1f}s {:>10.1f}s".format(
        "Wall-clock (s)", raw_t, grnd_t, opt_t))
    print()

    # Summary verdict
    out_raw  = raw_u["output_tokens"]
    out_grnd = grnd_u["output_tokens"]
    out_opt  = opt_u["output_tokens"]

    if out_raw > 0:
        pct_grnd = (out_raw - out_grnd) / out_raw * 100
        pct_opt  = (out_raw - out_opt)  / out_raw * 100
        print("  OUTPUT TOKEN REDUCTION vs raw:")
        print("    grounded:  {0:+.1f}%".format(-pct_grnd))
        print("    optimized: {0:+.1f}%".format(-pct_opt))
        if pct_opt > pct_grnd:
            print("    => output constraints saved {0:.1f}pp on top of grounding".format(
                pct_opt - pct_grnd))
        elif pct_opt == pct_grnd:
            print("    => output constraints had no measurable effect (scope={0})".format(scope))
        else:
            print("    => constraints increased output (unexpected)")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def _run_one(prompt: str, out_path: Path, cwd: str, tool: str,
             session_id: str | None = None) -> tuple[float, int]:
    if tool == "claude-code":
        return run_claude_code(prompt, out_path, cwd, session_id=session_id)
    return run_codex(prompt, out_path, cwd)


def _parse_one(out_path: Path, tool: str) -> dict:
    if tool == "claude-code":
        return parse_usage_claude(out_path)
    return parse_usage(out_path)


def _cost(u: dict, tool: str) -> float:
    return claude_cost(u) if tool == "claude-code" else codex_cost(u)


def _ext(tool: str) -> str:
    return ".json" if tool == "claude-code" else ".jsonl"


def run_test(prompt_def: dict, tool: str = "codex") -> None:
    pid = prompt_def["id"]
    raw = prompt_def["raw"]
    label = prompt_def["label"]
    ext = _ext(tool)

    OUT_DIR.mkdir(exist_ok=True)
    tool_dir = OUT_DIR / tool
    tool_dir.mkdir(exist_ok=True)
    raw_out  = tool_dir / "{0}_raw{1}".format(pid, ext)
    grnd_out = tool_dir / "{0}_grounded{1}".format(pid, ext)
    opt_out  = tool_dir / "{0}_optimized{1}".format(pid, ext)

    print("\n[{0}] Preparing prompts ({1})...".format(pid, tool))
    prompts = prepare_prompts(raw, HTTPX_DIR, tool=tool)
    print("  intent={0}  scope={1}".format(prompts["intent"], prompts["scope"]))
    print("  raw={0} chars  grounded={1} chars  optimized={2} chars".format(
        len(raw), len(prompts["grounded"]), len(prompts["optimized"])))
    if prompts["suffix"]:
        print("  [+] Output constraints appended ({0} chars)".format(len(prompts["suffix"])))

    print("[{0}] Running {1} RAW...".format(pid, tool))
    raw_t, _ = _run_one(raw, raw_out, HTTPX_DIR, tool)
    print("  Done {0:.1f}s".format(raw_t))

    print("[{0}] Running {1} GROUNDED...".format(pid, tool))
    grnd_t, _ = _run_one(prompts["grounded"], grnd_out, HTTPX_DIR, tool)
    print("  Done {0:.1f}s".format(grnd_t))

    print("[{0}] Running {1} OPTIMIZED...".format(pid, tool))
    opt_t, _ = _run_one(prompts["optimized"], opt_out, HTTPX_DIR, tool)
    print("  Done {0:.1f}s".format(opt_t))

    raw_u  = _parse_one(raw_out,  tool)
    grnd_u = _parse_one(grnd_out, tool)
    opt_u  = _parse_one(opt_out,  tool)

    print_result(label, prompts, raw_u, grnd_u, opt_u, raw_t, grnd_t, opt_t, tool=tool)


def reprint(prompt_def: dict, tool: str = "codex") -> None:
    pid = prompt_def["id"]
    raw = prompt_def["raw"]
    label = prompt_def["label"]
    ext = _ext(tool)
    tool_dir = OUT_DIR / tool
    raw_out  = tool_dir / "{0}_raw{1}".format(pid, ext)
    grnd_out = tool_dir / "{0}_grounded{1}".format(pid, ext)
    opt_out  = tool_dir / "{0}_optimized{1}".format(pid, ext)

    if not raw_out.exists():
        print("  [skip] missing output for {0}/{1}".format(tool, pid))
        return

    prompts = prepare_prompts(raw, HTTPX_DIR, tool=tool)
    raw_u  = _parse_one(raw_out,  tool)
    grnd_u = _parse_one(grnd_out, tool) if grnd_out.exists() else raw_u
    opt_u  = _parse_one(opt_out,  tool) if opt_out.exists()  else grnd_u
    print_result(label, prompts, raw_u, grnd_u, opt_u, 0.0, 0.0, 0.0, tool=tool)


# ---------------------------------------------------------------------------
# Dry-run: just show what prompts would be sent
# ---------------------------------------------------------------------------

def dry_run_prompts(prompt_def: dict, tool: str = "codex") -> None:
    pid = prompt_def["id"]
    raw = prompt_def["raw"]
    label = prompt_def["label"]

    print("\n[{0}] {1}".format(pid, label))
    print("-" * 60)
    prompts = prepare_prompts(raw, HTTPX_DIR, tool=tool)
    print("Intent: {0}  Scope: {1}".format(prompts["intent"], prompts["scope"]))
    print()
    print("--- RAW ({0} chars) ---".format(len(raw)))
    print(raw)
    print()
    print("--- GROUNDED ({0} chars) ---".format(len(prompts["grounded"])))
    print(prompts["grounded"][:600] + ("..." if len(prompts["grounded"]) > 600 else ""))
    print()
    print("--- OPTIMIZED ({0} chars) ---".format(len(prompts["optimized"])))
    print(prompts["optimized"][:600] + ("..." if len(prompts["optimized"]) > 600 else ""))
    if prompts["suffix"]:
        print("\n  [Output constraint appended]:")
        print(prompts["suffix"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-idx", default="all",
                        help="0-3 or 'all' (default)")
    parser.add_argument("--tool", default="codex", choices=list(TOOLS),
                        help="Downstream tool: codex (default) or claude-code")
    parser.add_argument("--reprint", action="store_true",
                        help="Re-display from saved output without re-running")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prepared prompts without running the tool")
    args = parser.parse_args()

    if args.prompt_idx == "all":
        targets = PROMPTS
    else:
        targets = [PROMPTS[int(args.prompt_idx)]]

    import functools
    if args.dry_run:
        fn = functools.partial(dry_run_prompts, tool=args.tool)
    elif args.reprint:
        fn = functools.partial(reprint, tool=args.tool)
    else:
        fn = functools.partial(run_test, tool=args.tool)

    for p in targets:
        fn(p)

    if not args.dry_run:
        print("\nOutput files:", OUT_DIR / args.tool)


if __name__ == "__main__":
    main()
