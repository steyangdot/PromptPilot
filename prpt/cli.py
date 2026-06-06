"""CLI entry-point: argument parser, main loop, install-hook subcommand."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Optional, Sequence

from prpt.core.constants import DEFAULT_LOG_FILE, DEFAULT_TARGET_MODEL, HELP_TEXT, MODEL_PRICING
from prpt.core.types import NormalizedRequest, TokenStats
from prpt.core.utils import maybe_log_run, write_stderr
from prpt.adapters.factory import AdapterFactory
from prpt.normalizers.base import (
    SemanticValidator, build_final_downstream_prompt, build_output_suffix, create_normalizer,
)
from prpt.repo.collector import RepoContextCollector
from prpt.session import append_turn, clear_session, load_recent_turns, session_path_for
from prpt.stats import print_stats
from prpt.ui import print_review, print_token_stats


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw = list(argv) if argv is not None else sys.argv[1:]

    # Handle subcommands before the main parser (avoids argparse
    # treating prompt words as invalid subcommand choices).
    if raw and raw[0] == "new-session":
        ns_parser = argparse.ArgumentParser(prog="prpt new-session")
        ns_parser.add_argument("--cwd", default=os.getcwd())
        ns = ns_parser.parse_args(raw[1:])
        ns.subcommand = "new-session"
        return ns

    if raw and raw[0] in ("setup", "doctor"):
        sd_parser = argparse.ArgumentParser(
            prog="prpt " + raw[0],
            description=(
                "One-time onboarding: probe Python / coding-agent CLI / auth, "
                "install deps if needed, run a smoke test."
                if raw[0] == "setup"
                else "Re-run setup checks (no install). Reports what's set up and what's missing."
            ),
        )
        ns = sd_parser.parse_args(raw[1:])
        ns.subcommand = raw[0]
        return ns

    if raw and raw[0] == "install-hook":
        hook_parser = argparse.ArgumentParser(prog="prpt install-hook")
        hook_parser.add_argument("--global", dest="global_hook", action="store_true",
                                 help="Install into ~/.claude/settings.json instead of project-level")
        hook_parser.add_argument("--tool", default="claude-code",
                                 choices=["claude-code", "codex"],
                                 help="Coding agent to wire the hook for (default: claude-code)")
        hook_parser.add_argument("--cwd", default=os.getcwd())
        ns = hook_parser.parse_args(raw[1:])
        ns.subcommand = "install-hook"
        return ns

    if raw and raw[0] == "stats":
        stats_parser = argparse.ArgumentParser(prog="prpt stats")
        stats_parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="JSONL log path")
        stats_parser.add_argument("--last", type=int, default=None, metavar="N",
                                  help="Show stats for the last N runs only")
        stats_parser.add_argument("--theme", default="plain", choices=["plain", "dark"],
                                  help="Terminal output theme")
        ns = stats_parser.parse_args(raw[1:])
        ns.subcommand = "stats"
        return ns

    if raw and raw[0] == "checkpoint":
        cp_parser = argparse.ArgumentParser(
            prog="prpt checkpoint",
            description="Snapshot the current PromptPilot session to a markdown handoff doc.")
        cp_parser.add_argument("--to", dest="out_path", default="handoff.md",
                               help="Output path (default: ./handoff.md)")
        cp_parser.add_argument("--cwd", default=os.getcwd(),
                               help="Repo whose session to checkpoint (default: cwd)")
        cp_parser.add_argument("--clear", action="store_true",
                               help="Also clear the session after writing the handoff (default: keep)")
        ns = cp_parser.parse_args(raw[1:])
        ns.subcommand = "checkpoint"
        return ns

    if raw and raw[0] == "bootstrap":
        bs_parser = argparse.ArgumentParser(
            prog="prpt bootstrap",
            description="Bootstrap a fresh PromptPilot session from an existing handoff.md.")
        bs_parser.add_argument("--from", dest="in_path", default="handoff.md",
                               help="Path to handoff.md to bootstrap from (default: ./handoff.md)")
        bs_parser.add_argument("--cwd", default=os.getcwd(),
                               help="Repo whose session to populate (default: cwd)")
        bs_parser.add_argument("--append", action="store_true",
                               help="Add to existing session instead of clearing first (default: clear)")
        ns = bs_parser.parse_args(raw[1:])
        ns.subcommand = "bootstrap"
        return ns

    if raw and raw[0] == "restart":
        rs_parser = argparse.ArgumentParser(
            prog="prpt restart",
            description="One-shot: checkpoint current session, clear it, bootstrap fresh from the handoff.")
        rs_parser.add_argument("--to", dest="out_path", default="handoff.md",
                               help="Where to write the handoff (default: ./handoff.md)")
        rs_parser.add_argument("--cwd", default=os.getcwd(),
                               help="Repo whose session to restart (default: cwd)")
        ns = rs_parser.parse_args(raw[1:])
        ns.subcommand = "restart"
        return ns

    # Short-circuit: --advanced-help prints help text including the flags
    # hidden from the normal --help. Implemented before argparse so we can
    # show every flag (argparse SUPPRESS makes them un-printable otherwise).
    if raw and raw[0] in ("--advanced-help", "-H"):
        print(_ADVANCED_HELP)
        sys.exit(0)

    parser = argparse.ArgumentParser(
        prog="prpt",
        description="PromptPilot - SLM-powered control plane for AI coding CLIs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Common commands:\n"
            "  prpt \"fix the flaky payment test\"          # auto-detects claude/codex\n"
            "  prpt --dry-run \"refactor auth, no API changes\"\n"
            "  prpt --tool codex \"add dark mode\"\n"
            "  prpt setup                                 # one-time onboarding (post-install)\n"
            "  prpt doctor                                # re-check setup\n"
            "  prpt install-hook                          # wire into Claude Code\n"
            "  prpt install-hook --tool codex             # wire into Codex\n"
            "\n"
            "Run `prpt --advanced-help` to see researcher/internal flags."
        ),
    )

    parser.add_argument("prompt", nargs="*", help="Raw developer prompt")
    parser.add_argument(
        "--normalizer", default="slm",
        choices=["heuristic", "slm", "slm-anthropic", "slm-anthropic-v2", "slm-openai", "slm-openai-v2", "slm-subscription", "slm-subscription-v2"],
        metavar="{slm,heuristic}",
        help="slm (default; auto-detects an available SLM backend), "
             "heuristic (rule-based, no API/auth needed). "
             "Other backends accepted but hidden from help; see --advanced-help.",
    )
    parser.add_argument("--api-key", default=None, help="API key for the SLM / tool adapter")
    parser.add_argument("--tool", default="auto",
                        help="Downstream tool: auto (default; picks claude-code or codex from PATH), "
                             "claude-code (alias: claude), codex, anthropic, openai, echo, or any CLI")
    parser.add_argument("--model", default=None, help="Model for --tool anthropic/openai")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max output tokens for API adapters")
    parser.add_argument("--tool-arg", action="append", help="Extra arg for downstream tool (repeatable)")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for repo context")
    parser.add_argument("--dry-run", action="store_true", help="Print final prompt without executing")
    parser.add_argument("--auto", action="store_true", help="Skip review prompt when safe")
    parser.add_argument("--pass-through", action="store_true", help="Forward raw prompt unchanged")
    parser.add_argument("--high-stakes", action="store_true", help="Conservative mode")
    parser.add_argument("--let-slm-answer", action="store_true",
                        help="On explain-style prompts, offer to let the SLM answer directly instead "
                             "of forwarding to the coding agent (interactive). Also enabled via "
                             "PROMPTPILOT_LET_SLM_ANSWER=1.")
    parser.add_argument("--verbose", action="store_true", help="Verbose stderr logging")

    # Hidden / advanced flags (kept working, suppressed in --help).
    parser.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--show-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--compare", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-repo-context", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gate-session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-runs", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help=argparse.SUPPRESS)
    parser.add_argument("--target-model", default=DEFAULT_TARGET_MODEL, help=argparse.SUPPRESS)
    parser.add_argument("--theme", default="plain", choices=["plain", "dark"], help=argparse.SUPPRESS)

    ns = parser.parse_args(raw)
    ns.subcommand = None
    return ns


# Help text shown by `prpt --advanced-help` (or `-H`). Documents the hidden
# flags so they remain discoverable for researchers / power users.
_ADVANCED_HELP = """\
prpt --advanced-help - all flags including hidden ones

The flags below are accepted but suppressed from the main --help to keep
onboarding focused. They are stable and supported, just researcher/internal.

  --strict              Always review when ambiguity exists.
  --show-json           Print the normalization JSON before the final prompt.
  --compare             Side-by-side token comparison: raw vs optimized; no exec.
  --no-repo-context     Skip loading repo file contents for the SLM.
  --gate-session        Skip loading prior session turns when the current prompt
                        doesn't back-reference them. Saves ~5% input tokens at
                        ~5% cost-per-success penalty (chain4 N=10 with v2
                        memory_record). Heavier savings on long-session
                        workloads. Adds one ~$0.0002 Haiku classifier call.
  --log-runs            Append runs to a JSONL log.
  --log-file PATH       JSONL log path (default: .promptpilot_runs.jsonl).
  --target-model NAME   Model to compute savings against.
  --theme {plain,dark}  Terminal output theme.

Normalizer aliases (--normalizer):
  slm                   Default. Auto-detects ANTHROPIC_API_KEY, OPENAI_API_KEY,
                        Max OAuth, codex login - whichever is set. SDK keys use
                        the v2 (JSON spec + routing) normalizers.
  heuristic             Rule-based, no API/auth needed.
  slm-anthropic         Force Anthropic SDK normalizer (legacy v1 prose).
  slm-anthropic-v2      Force Anthropic JSON execution-spec normalizer (routing
                        + clarify).
  slm-openai            Force OpenAI SDK normalizer (legacy v1 prose).
  slm-openai-v2         Force OpenAI JSON execution-spec normalizer (routing +
                        clarify).
  slm-subscription      Max OAuth / ChatGPT normalizer, legacy v1 prose (no API
                        key required).
  slm-subscription-v2   Max OAuth / ChatGPT normalizer with JSON spec (routing +
                        clarify); the auto-detect default for subscription auth.

Hidden subcommands and env vars:
  PROMPTPILOT_JUDGE=max|codex|anthropic|openai
                        Force the judge backend (handoff/restart path).
  PROMPTPILOT_LET_SLM_ANSWER=1
                        Same as --let-slm-answer.
  CLAUDE_MODEL=opus|sonnet|haiku
                        Override the Claude model in the chain harness.
  USE_MAX_AUTH=1        Chain harness uses Max OAuth instead of --bare.

See QUICKSTART.md and the GitHub wiki for the long-form docs."""


# ---------------------------------------------------------------------------
# install-hook subcommand
# ---------------------------------------------------------------------------

def _install_hook(args: argparse.Namespace) -> int:
    """Write hook config into the right settings file for the target tool.

    - --tool claude-code (default): writes a UserPromptSubmit hook to
      .claude/settings.local.json (or ~/.claude/settings.json with --global).
    - --tool codex: writes a UserPromptSubmit hook to .codex/hooks.json in
      the project (codex reads project-local config by convention).
    """
    hook_script = os.path.join(os.path.dirname(__file__), "hooks", "optimize_prompt.py")
    hook_script = os.path.abspath(hook_script).replace("\\", "/")
    tool = getattr(args, "tool", "claude-code") or "claude-code"

    if tool == "codex":
        # Codex uses project-local .codex/hooks.json. --global is meaningless
        # there; mention it and proceed with project-local.
        if getattr(args, "global_hook", False):
            print("Note: --global has no effect for codex; writing project-local "
                  ".codex/hooks.json instead.")
        settings_path = os.path.join(args.cwd, ".codex", "hooks.json")
    else:
        if args.global_hook:
            settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
        else:
            settings_path = os.path.join(args.cwd, ".claude", "settings.local.json")

    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        settings = {}

    hook_entry = {
        "hooks": [{
            "type": "command",
            "command": "python {0}".format(hook_script),
            "timeout": 30,
            "statusMessage": "Optimizing prompt with SLM...",
        }]
    }

    hooks = settings.setdefault("hooks", {})
    existing = hooks.get("UserPromptSubmit", [])

    for entry in existing:
        for h in entry.get("hooks", []):
            if "optimize_prompt" in h.get("command", ""):
                print("Hook already installed in {0}".format(settings_path))
                return 0

    existing.append(hook_entry)
    hooks["UserPromptSubmit"] = existing

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print("Installed {tool} hook into {p}".format(tool=tool, p=settings_path))
    print("Command: python {0}".format(hook_script))
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_route(normalizer) -> str:
    """Return the routing decision for this normalize() call.

    v2 normalizers (slm-anthropic-v2 / slm-openai-v2 / slm-subscription-v2) populate
    `_last_spec.route` with one of {answer, act, clarify, passthrough}. v1 normalizers don't carry a spec;
    fall back to deriving from intent:
      - intent="explain" -> "answer" (offer SLM direct response)
      - intent="act" or unset -> "act" (forward to agent)

    Returns one of {answer, act, clarify, passthrough}; defaults to "act"
    when nothing else is determinable.
    """
    spec = getattr(normalizer, "_last_spec", None)
    if spec is not None:
        route = getattr(spec, "route", None)
        if route in ("answer", "act", "clarify", "passthrough"):
            return route
    intent = getattr(normalizer, "_last_intent", None) or "act"
    return "answer" if intent == "explain" else "act"


def _append_output_constraints(prompt: str, normalizer, tool: str) -> str:
    """Append tool-aware output format constraints for act+pinpoint/localized tasks."""
    intent = getattr(normalizer, "_last_intent", None)
    scope = getattr(normalizer, "_last_scope", None)
    if intent != "act":
        return prompt
    suffix = build_output_suffix(scope, tool)
    return prompt + "\n\n" + suffix if suffix else prompt


def _input_or_default(prompt: str, default: str, reason: str) -> str:
    """Read an interactive choice, or return the default in non-TTY runs."""
    if sys.stdin.isatty():
        return input(prompt).strip() or default
    write_stderr("[promptpilot] non-interactive stdin; defaulting to {0}.".format(reason))
    return default


def _build_assistant_record(normalizer, normalized, modified_files) -> str:
    """Build the assistant session-turn record from spec.memory_record (v2)
    or the SLM rewrite (v1), prefixed with the adapter's modified files.

    v2 normalizers (slm-anthropic-v2 / slm-openai-v2 / slm-subscription-v2) populate `_last_spec.memory_record` with a
    pre-run one-sentence summary of intent + constraints. That's higher-signal
    for future referential turns than the verbose rewritten prompt, so it
    leads when present. v1 normalizers don't carry a spec; fall back to the
    truncated rewrite (current behavior).

    `modified_files` (from `adapter.last_modified_files`) is post-run ground
    truth and prefixes the summary when non-empty. Total stays under 600
    chars so MAX_TURNS history stays bounded.
    """
    spec = getattr(normalizer, "_last_spec", None)
    summary = ""
    if spec is not None:
        summary = (getattr(spec, "memory_record", "") or "").strip()
    if not summary:
        summary = normalized.normalized_prompt

    modified_files = list(modified_files or [])
    if modified_files:
        files_str = ", ".join(modified_files[:8])
        if len(modified_files) > 8:
            files_str += ", ... (+{0} more)".format(len(modified_files) - 8)
        content = "Modified: {0}\n{1}".format(files_str, summary[:400])
    else:
        content = summary[:600]
    return content[:600]


def _log_kwargs(args: argparse.Namespace, repo, raw_prompt, final_prompt, exit_code, mode,
                normalized=None, validation=None, token_stats=None) -> dict:
    """Build kwargs dict for maybe_log_run."""
    return dict(
        mode=mode, tool=args.tool, normalizer=args.normalizer, cwd=args.cwd,
        repo=repo, raw_prompt=raw_prompt, final_prompt=final_prompt,
        exit_code=exit_code, auto=args.auto, strict=args.strict,
        dry_run=args.dry_run, pass_through=args.pass_through,
        normalized=normalized, validation=validation, token_stats=token_stats,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    # Sub-commands
    if args.subcommand == "new-session":
        clear_session(args.cwd)
        print("Session cleared: {0}".format(session_path_for(args.cwd)))
        return 0
    if args.subcommand in ("setup", "doctor"):
        from prpt.setup import run_setup
        return run_setup(mode=args.subcommand)
    if args.subcommand == "install-hook":
        return _install_hook(args)
    if args.subcommand == "stats":
        print_stats(args.log_file, last_n=args.last, theme=args.theme)
        return 0
    if args.subcommand == "checkpoint":
        from prpt.handoff import write_handoff
        from pathlib import Path
        try:
            info = write_handoff(args.cwd, Path(args.out_path), clear_after=args.clear)
        except RuntimeError as e:
            write_stderr(f"checkpoint failed: {e}\n")
            return 1
        print("Wrote {p} ({n} turns summarized, ${c:.4f}, {w:.1f}s){clr}".format(
            p=info["out_path"], n=info["turns_summarized"],
            c=info["cost_usd"], w=info["walltime_s"],
            clr=" [session cleared]" if info["cleared"] else ""))
        return 0
    if args.subcommand == "bootstrap":
        from prpt.handoff import read_handoff_into_session
        from pathlib import Path
        try:
            info = read_handoff_into_session(args.cwd, Path(args.in_path), append=args.append)
        except RuntimeError as e:
            write_stderr(f"bootstrap failed: {e}\n")
            return 1
        print("Bootstrapped session from {p}{clr} (user msg {u}c, assistant msg {a}c)".format(
            p=info["in_path"],
            clr=" [prior session cleared]" if info["cleared"] else " [appended]",
            u=info["user_msg_chars"], a=info["assistant_msg_chars"]))
        return 0
    if args.subcommand == "restart":
        from prpt.handoff import restart_session
        from pathlib import Path
        try:
            info = restart_session(args.cwd, Path(args.out_path))
        except RuntimeError as e:
            write_stderr(f"restart failed: {e}\n")
            return 1
        print("Restarted: snapshot to {p} ({n} turns, ${c:.4f}, {w:.1f}s) "
              "and bootstrapped fresh session (user {u}c, assistant {a}c)".format(
                  p=info["out_path"], n=info["turns_summarized"],
                  c=info["cost_usd"], w=info["walltime_s"],
                  u=info["user_msg_chars"], a=info["assistant_msg_chars"]))
        return 0

    # Prompt
    raw_prompt = " ".join(args.prompt).strip() if args.prompt else ""
    if not raw_prompt:
        write_stderr(HELP_TEXT)
        return 2

    repo = RepoContextCollector().collect(args.cwd)

    # Pass-through mode
    if args.pass_through:
        final_prompt = raw_prompt
        if args.dry_run:
            print(final_prompt)
            maybe_log_run(args.log_file, args.log_runs,
                          **_log_kwargs(args, repo, raw_prompt, final_prompt, 0, "pass_through_dry_run"))
            return 0
        exit_code = AdapterFactory.create(args).run(final_prompt, args)
        append_turn(args.cwd, "user", raw_prompt)
        maybe_log_run(args.log_file, args.log_runs,
                      **_log_kwargs(args, repo, raw_prompt, final_prompt, exit_code, "pass_through"))
        return exit_code

    # Normalizer.
    #
    # Default is "slm" (auto-detect Anthropic/OpenAI/subscription) - the
    # product's core path. If no SLM backend is available (no API keys, no
    # Max OAuth, no codex login), `create_normalizer("slm")` raises
    # RuntimeError; we fall back to heuristic with a clear note rather than
    # erroring out. Users who pass --normalizer heuristic explicitly skip
    # the fallback path.
    try:
        normalizer = create_normalizer(
            args.normalizer, api_key=args.api_key,
            load_repo_content=not args.no_repo_context,
        )
    except (ImportError, RuntimeError) as exc:
        if args.normalizer == "slm":
            write_stderr(
                "[promptpilot] No SLM backend available ({0}); "
                "falling back to --normalizer heuristic. Set up an API key "
                "(ANTHROPIC_API_KEY / OPENAI_API_KEY) or run `claude auth "
                "login --claudeai` for full SLM functionality.\n".format(exc)
            )
            args.normalizer = "heuristic"
            normalizer = create_normalizer(
                "heuristic", api_key=args.api_key,
                load_repo_content=not args.no_repo_context,
            )
        else:
            write_stderr("Error: {0}".format(exc))
            return 1

    # Session: prepend recent turns so SLM can resolve references across invocations.
    #
    # With --gate-session, first ask the SLM a cheap ~$0.0002 question: does this
    # prompt back-reference prior turns? If no, skip the load entirely. Original
    # validation (pre-v2-#4): chain4 N=10 + chain5 N=15, ~26% input-token savings
    # at zero quality cost. Re-validated 2026-05-17 under v2's clean memory_record
    # (`_gate_chain4_n10/`): savings shrank to ~5% input tokens at ~5% cps penalty
    # because the v2 record made session itself short - the gate has less to skip.
    # Still useful for long-session / burst-of-independent-prompts workloads.
    # Gate is fail-safe: classifier errors default to loading history rather
    # than silently dropping memory.
    prompt_for_slm = raw_prompt
    gate_active = args.gate_session and args.normalizer != "heuristic"
    if args.gate_session and args.normalizer == "heuristic":
        write_stderr(
            "[promptpilot] --gate-session requires an SLM normalizer; ignored for heuristic."
        )

    if args.normalizer != "heuristic":
        load_history = True
        if gate_active and hasattr(normalizer, "is_referential"):
            # Pass args.cwd so the subprocess-backed referential classifier
            # (subscription normalizer) resolves project context from the
            # --cwd target, not the process cwd. SDK normalizers ignore it.
            referential = normalizer.is_referential(raw_prompt, cwd=args.cwd)
            load_history = referential
            write_stderr(
                "[promptpilot] gate: prompt {0} (history {1})".format(
                    "is referential" if referential else "is self-contained",
                    "loaded" if referential else "skipped",
                )
            )

        if load_history:
            recent_turns = load_recent_turns(args.cwd)
            if recent_turns:
                history = "\n".join(recent_turns)
                prompt_for_slm = (
                    "[Recent conversation]\n{history}\n\n[Current request]\n{prompt}"
                ).format(history=history, prompt=raw_prompt)
                # One user-visible message per invocation telling them what's
                # being carried forward. Helps the user notice when a session is
                # stale and decide whether to `prpt new-session`.
                n_pairs = len(recent_turns) // 2
                write_stderr(
                    "[promptpilot] session: carrying {0} prior turn{1} "
                    "(run `prpt new-session` to clear)".format(
                        n_pairs, "" if n_pairs == 1 else "s"
                    )
                )

    normalized = normalizer.normalize(prompt_for_slm, repo, high_stakes=args.high_stakes)
    validation = SemanticValidator().validate(normalized)

    # v2 control plane: resolve routing decision from the spec (with v1 intent
    # fallback). Drives whether we ask the agent at all, ask the user for
    # clarification, or pass the raw prompt through unrewritten.
    route = _resolve_route(normalizer)
    if route != "act":
        write_stderr("[promptpilot] route={0}".format(route))

    if args.show_json:
        print(json.dumps(asdict(normalized), indent=2))
        print()

    # route=clarify: SLM judged the prompt ambiguous. Print the clarifying
    # question and exit so the user can refine and re-run. --auto and
    # --dry-run still see the message but fall through to the act path so
    # automated runs don't silently bail.
    if route == "clarify" and not args.auto and not args.dry_run \
            and not getattr(args, "compare", False):
        write_stderr(
            "[promptpilot] SLM judged the prompt ambiguous "
            "-- clarification needed:"
        )
        print(normalized.normalized_prompt)
        write_stderr("\n[promptpilot] Refine the prompt and re-run.")
        maybe_log_run(args.log_file, args.log_runs,
                      **_log_kwargs(args, repo, raw_prompt, normalized.normalized_prompt,
                                    0, "clarify", normalized, validation, None))
        return 0

    # route=passthrough: SLM judged rewriting risky; use the raw prompt as-is.
    # Skip output-constraint suffix because the user wrote what they wanted.
    if route == "passthrough":
        write_stderr("[promptpilot] route=passthrough -- using raw prompt unmodified.")
        final_prompt = raw_prompt
    else:
        spec = getattr(normalizer, "_last_spec", None)
        target_files = getattr(spec, "target_files", None) if spec is not None else None
        final_prompt = build_final_downstream_prompt(normalized, repo, target_files=target_files)
        final_prompt = _append_output_constraints(final_prompt, normalizer, args.tool)
    should_review = args.strict or normalized.needs_review or validation.recommended_action == "review"

    # Token stats (SLM path only)
    token_stats: Optional[TokenStats] = None
    if hasattr(normalizer, "compute_token_stats"):
        token_stats = normalizer.compute_token_stats(
            raw_prompt, final_prompt, target_model=args.target_model,
        )

    # route=answer: warn + offer SLM direct answer (skip the agent call).
    # The route is resolved from the v2 spec when available, falling back to
    # intent="explain" for v1 normalizers. Opt-in only -- previous default
    # showed a 1/2/3 dialog on every explain prompt, which was disruptive in
    # an interactive CLI. Enable via --let-slm-answer or
    # PROMPTPILOT_LET_SLM_ANSWER=1.
    can_answer_directly = hasattr(normalizer, "answer_directly")
    let_slm_answer = (
        getattr(args, "let_slm_answer", False)
        or os.environ.get("PROMPTPILOT_LET_SLM_ANSWER", "").strip() in {"1", "true", "TRUE", "yes"}
    )
    if (route == "answer" and can_answer_directly and let_slm_answer
            and not args.auto and not args.dry_run
            and not getattr(args, "compare", False)):
        write_stderr(
            "[promptpilot] Explanation prompt detected (task_type={0}).\n"
            "  Grounded prompts add overhead for explain tasks: downstream produces\n"
            "  more output, not less. Consider letting the SLM answer directly instead."
            .format(normalized.task_type)
        )
        print("Options:")
        print("  [1] Proceed - forward grounded prompt to {0}".format(args.tool))
        print("  [2] SLM answers directly - fast, cheap, no downstream call")
        print("  [3] Abort")
        explain_choice = _input_or_default(
            "Choice [1/2/3, default=1]: ", "1", "choice 1"
        )
        if explain_choice == "2":
            answer = normalizer.answer_directly(raw_prompt, repo)
            print(answer)
            append_turn(args.cwd, "user", raw_prompt)
            append_turn(args.cwd, "assistant", answer[:600])
            maybe_log_run(args.log_file, args.log_runs,
                          **_log_kwargs(args, repo, raw_prompt, answer, 0, "slm_direct_answer",
                                        normalized, validation, token_stats))

            # Continuation: suggest actions or accept custom prompt (warm context)
            print()
            suggestions = []
            if hasattr(normalizer, "suggest_actions"):
                write_stderr("[promptpilot] Generating suggested follow-up actions...")
                suggestions = normalizer.suggest_actions(answer, repo)

            if suggestions:
                print("Suggested actions:")
                for i, s in enumerate(suggestions, 1):
                    print("  [{0}] {1}".format(i, s))
                print("  [c] Custom prompt")
                print("  [Enter] Exit")
                try:
                    pick = input("Choice: ").strip()
                except (EOFError, KeyboardInterrupt):
                    pick = ""
                if not pick:
                    return 0
                if pick in {"1", "2", "3"} and int(pick) <= len(suggestions):
                    follow_up = suggestions[int(pick) - 1]
                elif pick.lower() == "c":
                    try:
                        follow_up = input("Enter prompt: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        follow_up = ""
                    if not follow_up:
                        return 0
                else:
                    follow_up = pick  # treat as custom prompt directly
            else:
                try:
                    follow_up = input("Take action? Enter a prompt (or press Enter to exit): ").strip()
                except (EOFError, KeyboardInterrupt):
                    follow_up = ""
                if not follow_up:
                    return 0

            # Re-normalize using warm context (context_block cached in normalizer)
            write_stderr("[promptpilot] Re-using warm context for follow-up...")
            normalized = normalizer.normalize(follow_up, repo, high_stakes=args.high_stakes)
            validation = SemanticValidator().validate(normalized)
            spec = getattr(normalizer, "_last_spec", None)
            target_files = getattr(spec, "target_files", None) if spec is not None else None
            final_prompt = build_final_downstream_prompt(normalized, repo, target_files=target_files)
            final_prompt = _append_output_constraints(final_prompt, normalizer, args.tool)

            token_stats = None
            if hasattr(normalizer, "compute_token_stats"):
                token_stats = normalizer.compute_token_stats(
                    follow_up, final_prompt, target_model=args.target_model,
                )
            if token_stats is not None:
                print_token_stats(token_stats, theme=getattr(args, "theme", "plain"))

            args.context_block = getattr(normalizer, "_last_context_block", None)
            adapter = AdapterFactory.create(args)
            exit_code = adapter.run(final_prompt, args)

            actual_usage = getattr(adapter, "last_usage", None)
            if actual_usage and token_stats is not None:
                target_price = MODEL_PRICING.get(
                    args.target_model, {"input": 15.00, "output": 75.00})
                actual_cost = actual_usage.get("total_cost_usd") or (
                    actual_usage["input_tokens"] * target_price["input"]
                    + actual_usage["output_tokens"] * target_price["output"]
                ) / 1_000_000
                token_stats.actual_input_tokens = actual_usage["input_tokens"]
                token_stats.actual_output_tokens = actual_usage["output_tokens"]
                token_stats.actual_total_cost_usd = actual_cost
                print_token_stats(token_stats, theme=getattr(args, "theme", "plain"))

            append_turn(args.cwd, "user", follow_up)
            modified = getattr(adapter, "last_modified_files", None) or []
            append_turn(args.cwd, "assistant",
                        _build_assistant_record(normalizer, normalized, modified))
            maybe_log_run(args.log_file, args.log_runs,
                          **_log_kwargs(args, repo, follow_up, final_prompt, exit_code,
                                        "warm_followup", normalized, validation, token_stats))
            return exit_code
        if explain_choice == "3":
            print("Aborted.")
            return 1

    # --compare: side-by-side token comparison, no execution
    if getattr(args, "compare", False):
        from prpt.ui import print_compare
        print_compare(raw_prompt, final_prompt, normalized, token_stats, args)
        maybe_log_run(args.log_file, args.log_runs,
                      **_log_kwargs(args, repo, raw_prompt, final_prompt, 0, "compare",
                                    normalized, validation, token_stats))
        return 0

    if args.dry_run:
        print("=== REVIEW ===")
        print_review(normalized, validation, theme=args.theme)
        if args.normalizer != "heuristic":
            repo_ctx = "yes" if not args.no_repo_context else "no (--no-repo-context)"
            print("=== SLM REWRITE (repo context: {0}) ===".format(repo_ctx))
            print("Original : {0}".format(normalized.original_prompt))
            print("Rewritten: {0}".format(normalized.normalized_prompt))
            print()
        if token_stats is not None:
            print_token_stats(token_stats, theme=args.theme)
        print("=== FINAL PROMPT ===")
        print(final_prompt)
        maybe_log_run(args.log_file, args.log_runs,
                      **_log_kwargs(args, repo, raw_prompt, final_prompt, 0, "wrapped_dry_run",
                                    normalized, validation, token_stats))
        return 0

    if should_review and not args.auto:
        print_review(normalized, validation, theme=args.theme)
        if token_stats is not None:
            print_token_stats(token_stats, theme=args.theme)
        choice = _input_or_default(
            "Proceed with this normalized request? [Y/n] ", "y", "yes"
        ).lower()
        if choice in {"n", "no"}:
            print("Aborted.")
            maybe_log_run(args.log_file, args.log_runs,
                          **_log_kwargs(args, repo, raw_prompt, final_prompt, 1, "wrapped_aborted",
                                        normalized, validation, token_stats))
            return 1

    args.context_block = getattr(normalizer, "_last_context_block", None)
    adapter = AdapterFactory.create(args)
    exit_code = adapter.run(final_prompt, args)

    # Attach actual usage if the adapter captured it
    actual_usage = getattr(adapter, "last_usage", None)
    if actual_usage and token_stats is not None:
        target_price = MODEL_PRICING.get(args.target_model, {"input": 15.00, "output": 75.00})
        # Use adapter's cache-aware cost when available; fall back to naive calculation
        actual_cost = actual_usage.get("total_cost_usd") or (
            actual_usage["input_tokens"] * target_price["input"]
            + actual_usage["output_tokens"] * target_price["output"]
        ) / 1_000_000
        token_stats.actual_input_tokens = actual_usage["input_tokens"]
        token_stats.actual_output_tokens = actual_usage["output_tokens"]
        token_stats.actual_total_cost_usd = actual_cost
        print_token_stats(token_stats, theme=args.theme)

    append_turn(args.cwd, "user", raw_prompt)
    # Assistant turn records (a) which files the downstream tool actually
    # modified (ground truth from the adapter) and (b) a short summary of
    # intent + constraints. v2 normalizers carry `spec.memory_record` for
    # (b); v1 falls back to the SLM rewrite. See `_build_assistant_record`.
    modified = getattr(adapter, "last_modified_files", None) or []
    append_turn(args.cwd, "assistant",
                _build_assistant_record(normalizer, normalized, modified))
    maybe_log_run(args.log_file, args.log_runs,
                  **_log_kwargs(args, repo, raw_prompt, final_prompt, exit_code, "wrapped",
                                normalized, validation, token_stats))
    return exit_code


def main_cli() -> None:
    """Entry-point for the `promptpilot` console script."""
    sys.exit(main())


if __name__ == "__main__":
    main_cli()
