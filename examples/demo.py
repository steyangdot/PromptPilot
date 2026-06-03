#!/usr/bin/env python3
"""PromptPilot demo — watch the SLM control layer reshape a raw prompt.

Runs with ZERO setup: no API key, no coding-agent install, no network. By
default it uses the offline heuristic normalizer so anyone (and CI) can see
PromptPilot's behavior in one command:

    python examples/demo.py

By default it stays offline (the heuristic normalizer) so it's free and CI-safe.
It auto-loads a local .env through prpt's own loader, so adding --slm uses your
key with no shell export or shim:

    python examples/demo.py              # offline heuristic (free, no network)
    python examples/demo.py --slm        # live small-model rewrite (~$0.002/example)
    python examples/demo.py --offline    # force heuristic even if a key is present
    python examples/demo.py --only 1     # run a single example
    python examples/demo.py --full       # show the full structured prompt for every example

The demo drives PromptPilot's real pipeline — create_normalizer -> normalize ->
SemanticValidator -> build_final_downstream_prompt — against a fixed synthetic
repo so the output is deterministic and reproducible regardless of where you
run it. Nothing here is hardcoded; every field shown is computed by prpt.
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Make `import prpt` work from a fresh clone without `pip install` — same
# bootstrap quickstart.py uses. (examples/ -> repo root.)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from prpt.core.dotenv import load_dotenv  # noqa: E402
from prpt.core.types import NormalizedRequest, RepoMetadata  # noqa: E402
from prpt.normalizers.base import (  # noqa: E402
    SemanticValidator,
    build_final_downstream_prompt,
    create_normalizer,
)


# ---------------------------------------------------------------------------
# Example fixtures
#
# Each example pairs a deliberately rough developer prompt with a synthetic
# repo so the story stays coherent and the output is byte-stable. The prompts
# are phrased the way people actually type them — run-on, mixed constraints,
# sometimes vague — which is exactly what the control layer is for.
# ---------------------------------------------------------------------------

_PAYMENTS_REPO = RepoMetadata(
    cwd="/home/dev/payments-api",
    branch="main",
    changed_files=["sync/order_worker.py", "tests/test_worker.py"],
    diff=None,
    dominant_language="Python",
    test_framework="pytest",
)


@dataclass
class Example:
    title: str
    blurb: str
    prompt: str
    repo: RepoMetadata


EXAMPLES: List[Example] = [
    Example(
        title="Bug fix with hard constraints",
        blurb="A precise request - PromptPilot pins the constraints before the agent starts.",
        prompt=(
            "the OrderSyncWorker keeps timing out under load and dropping events. find "
            "the root cause and fix the timeout. do not change the public API, keep the "
            "DB schema backward compatible, and ship a minimal patch with a regression "
            "test in tests/test_worker.py."
        ),
        repo=_PAYMENTS_REPO,
    ),
    Example(
        title="Vague request",
        blurb="An under-specified ask - PromptPilot flags the ambiguities instead of guessing.",
        prompt="something in the app is broken and users are complaining. can you fix it?",
        repo=_PAYMENTS_REPO,
    ),
    Example(
        title="Refactor with hard vs soft constraints",
        blurb="A mixed request - hard constraints and soft preferences are separated.",
        prompt=(
            "refactor the retry logic in ChargeService (billing/charge.py) to use "
            "exponential backoff, do not change the public function signatures, prefer "
            "the smallest safe change, and keep it backward compatible."
        ),
        repo=_PAYMENTS_REPO,
    ),
]


# ---------------------------------------------------------------------------
# Tiny ANSI palette (auto-disabled when not a TTY / NO_COLOR / --no-color)
# ---------------------------------------------------------------------------

class Palette:
    def __init__(self, enabled: bool) -> None:
        def code(value: str) -> str:
            return value if enabled else ""
        self.reset = code("\033[0m")
        self.dim = code("\033[2m")
        self.bold = code("\033[1m")
        self.cyan = code("\033[36m")
        self.green = code("\033[32m")
        self.yellow = code("\033[33m")
        self.magenta = code("\033[35m")
        self.blue = code("\033[34m")
        self.grey = code("\033[90m")


def _color_enabled(no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        # Enable ANSI escape processing in legacy Windows consoles.
        os.system("")
    return True


def _reconfigure_stdout_utf8() -> None:
    """Emit UTF-8 so box-drawing glyphs survive pipes / non-UTF consoles."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # py3.7+
    except (AttributeError, ValueError):
        pass


class Glyphs:
    """Structural glyphs, swappable to ASCII for limited terminals."""
    def __init__(self, unicode_ok: bool) -> None:
        if unicode_ok:
            self.h, self.v = "─", "│"
            self.tl, self.tr, self.bl, self.br = "┌", "┐", "└", "┘"
            self.arrow, self.dot, self.dash = "→", "·", "—"
        else:
            self.h, self.v = "-", "|"
            self.tl, self.tr, self.bl, self.br = "+", "+", "+", "+"
            self.arrow, self.dot, self.dash = "->", "*", "-"


_G = Glyphs(unicode_ok=True)  # replaced in main() once flags are parsed


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_WIDTH = 74


def _rule(p: Palette) -> str:
    return p.grey + (_G.h * _WIDTH) + p.reset


def _label(p: Palette, text: str) -> str:
    return "{0}{1}{2}".format(p.bold + p.cyan, text, p.reset)


def _field(p: Palette, name: str, items: List[str], empty: str = "(none)") -> List[str]:
    """Render a 'name: value(s)' block; long values wrap under an indent."""
    head = "    {0}{1:<18}{2}".format(p.dim, name, p.reset)
    if not items:
        return ["{0}{1}{2}".format(head, p.grey, empty) + p.reset]
    lines: List[str] = []
    indent = " " * 22
    for i, item in enumerate(items):
        wrapped = textwrap.wrap(item, width=_WIDTH - len(indent)) or [""]
        prefix = head if i == 0 else indent
        lines.append(prefix + wrapped[0])
        for cont in wrapped[1:]:
            lines.append(indent + cont)
    return lines


def _box(p: Palette, body: str) -> List[str]:
    inner = _WIDTH - 4
    out = ["    " + p.grey + _G.tl + (_G.h * (inner + 1)) + _G.tr + p.reset]
    for raw_line in body.splitlines() or [""]:
        for chunk in (textwrap.wrap(raw_line, width=inner) or [""]):
            out.append("    " + p.grey + _G.v + " " + p.reset
                       + "{0:<{1}}".format(chunk, inner) + p.grey + _G.v + p.reset)
    out.append("    " + p.grey + _G.bl + (_G.h * (inner + 1)) + _G.br + p.reset)
    return out


_SPAN_STOPWORDS = {"NOT", "AND", "OR", "THE", "BUT", "FOR", "WITH"}


def _meaningful_spans(spans: List[str], limit: int = 10) -> List[str]:
    """Keep identifier-like protected spans for display; drop prose-word noise.

    The v1 SLM normalizer fills protected_spans by re-scanning capitalized words
    out of its own rewrite, which surfaces junk like "Do", "Add", "Requirements".
    We keep spans that look like real anchors — paths, dotted names, multi-word
    phrases, snake_case / has-a-digit, CamelCase identifiers, or short ALL-CAPS
    acronyms (API, DB) — and drop bare single Capitalized words (deduping
    case-insensitively). Display only; never changes what prpt forwards.
    """
    out: List[str] = []
    seen = set()
    for raw in spans:
        s = (raw or "").strip()
        key = s.lower()
        if not s or key in seen or s.upper() in _SPAN_STOPWORDS:
            continue
        looks_meaningful = (
            " " in s                                  # phrase: "minimal patch"
            or "/" in s or "." in s                   # path: tests/test_worker.py
            or "_" in s or any(c.isdigit() for c in s)
            or any(c.isupper() for c in s[1:])        # CamelCase/acronym: OrderSyncWorker, API
        )
        if looks_meaningful:
            seen.add(key)
            out.append(s)
    return out[:limit]


# ---------------------------------------------------------------------------
# Route resolution (mirrors prpt.cli._resolve_route without importing a
# private symbol). The heuristic normalizer carries no spec/intent, so it
# always routes "act"; SLM normalizers may surface clarify/answer/passthrough.
# ---------------------------------------------------------------------------

def _route_of(normalizer) -> str:
    spec = getattr(normalizer, "_last_spec", None)
    if spec is not None:
        route = getattr(spec, "route", None)
        if route in ("answer", "act", "clarify", "passthrough"):
            return route
    intent = getattr(normalizer, "_last_intent", None) or "act"
    return "answer" if intent == "explain" else "act"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def _detect_slm_backend() -> Optional[str]:
    """Cheap, network-free detection of a usable SLM backend.

    Only checks for explicit signals (env API keys / forced judge). Subscription
    auth (Max / ChatGPT) can't be confirmed without a call, so we don't probe it
    here — pass --slm to use it.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY"
    if os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY"
    if os.environ.get("PROMPTPILOT_JUDGE", "").strip().lower() in ("max", "codex", "anthropic", "openai"):
        return "PROMPTPILOT_JUDGE"
    return None


def _resolve_normalizer_choice(args, p: Palette) -> str:
    """Decide heuristic vs slm and print a one-line note about why.

    Default is offline (free, CI-safe) even when a key is present; --slm opts in
    to the live rewrite. Since the demo auto-loads .env, a detected key earns a
    hint rather than silently spending money on a casual run.
    """
    if args.slm:
        return "slm"
    if args.offline:
        print("  {0}mode{1}  offline heuristic (forced via --offline; no network, no cost)"
              .format(p.dim, p.reset))
        return "heuristic"
    detected = _detect_slm_backend()
    if detected:
        print("  {0}mode{1}  offline heuristic. Detected {2}{3}{4} — add {5}--slm{6} for the "
              "live small-model rewrite (~$0.002/example)."
              .format(p.dim, p.reset, p.green, detected, p.reset, p.bold, p.reset))
    else:
        print("  {0}mode{1}  offline heuristic (no API key found). "
              "Set ANTHROPIC_API_KEY or pass {2}--slm{3} for the live small-model rewrite."
              .format(p.dim, p.reset, p.bold, p.reset))
    return "heuristic"


def _build_normalizer(choice: str, args, p: Palette):
    """Create the chosen normalizer, falling back to heuristic like the CLI does."""
    try:
        # load_repo_content=False keeps the SLM path deterministic and fast — it
        # uses the synthetic RepoMetadata we pass to normalize() rather than
        # reading files off disk.
        return create_normalizer(choice, load_repo_content=False), choice
    except (ImportError, RuntimeError) as exc:
        if choice != "heuristic":
            print("  {0}note{1}  SLM backend unavailable ({2}); falling back to heuristic."
                  .format(p.yellow, p.reset, exc))
            return create_normalizer("heuristic", load_repo_content=False), "heuristic"
        raise


# ---------------------------------------------------------------------------
# Per-example rendering
# ---------------------------------------------------------------------------

def _render_example(idx: int, total: int, ex: Example, normalizer,
                    p: Palette, show_full: bool) -> None:
    repo = ex.repo
    norm: NormalizedRequest = normalizer.normalize(ex.prompt, repo, high_stakes=False)
    SemanticValidator().validate(norm)  # exercises the same validation the CLI runs
    route = _route_of(normalizer)
    final_prompt = build_final_downstream_prompt(norm, repo)

    print()
    print(_rule(p))
    print(" {0}Example {1} of {2}{3}  {5}  {4}".format(
        p.bold, idx, total, p.reset, ex.title, _G.dot))
    print(" {0}{1}{2}".format(p.grey, ex.blurb, p.reset))
    print(_rule(p))
    print()

    print("  " + _label(p, "RAW PROMPT"))
    for line in textwrap.wrap(ex.prompt, width=_WIDTH - 4):
        print("    " + p.grey + line + p.reset)
    print()

    review = "{0}yes{1}".format(p.yellow, p.reset) if norm.needs_review else "no"
    print("  " + _label(p, "PROMPTPILOT")
          + "  route={0}{1}{2}  task={0}{3}{2}  confidence={0}{4}{2}  needs_review={5}".format(
              p.magenta, route, p.reset, norm.task_type, norm.confidence, review))
    print()

    print("  " + _label(p, "EXTRACTED"))
    for line in _field(p, "protected spans", _meaningful_spans(norm.protected_spans)):
        print(line)
    for line in _field(p, "hard constraints", norm.hard_constraints):
        print(line)
    for line in _field(p, "soft preferences", norm.soft_preferences):
        print(line)
    for line in _field(p, "requested output", norm.requested_output):
        print(line)
    for line in _field(p, "ambiguities", norm.ambiguities):
        print(line)
    if norm.assumptions:
        for line in _field(p, "assumptions", norm.assumptions):
            print(line)
    print()

    show_prompt = show_full or idx == 1
    if show_prompt:
        print("  " + _label(p, "STRUCTURED PROMPT") + p.grey
              + "  {0} forwarded to the coding agent".format(_G.arrow) + p.reset)
        for line in _box(p, final_prompt):
            print(line)
    else:
        n_lines = len(final_prompt.splitlines())
        print("  " + _label(p, "STRUCTURED PROMPT") + p.grey
              + "  {0}-line structured prompt built (run with --full to print it)".format(n_lines)
              + p.reset)
    print()


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python examples/demo.py",
        description="Watch PromptPilot's SLM control layer reshape a raw developer prompt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--slm", action="store_true",
                    help="Force the live small-model rewrite (needs an API key or subscription).")
    ap.add_argument("--offline", action="store_true",
                    help="Force the offline heuristic normalizer (no network, no cost).")
    ap.add_argument("--only", type=int, default=None, metavar="N",
                    help="Run only example N (1-based).")
    ap.add_argument("--full", action="store_true",
                    help="Print the full structured prompt for every example.")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI color.")
    ap.add_argument("--ascii", action="store_true",
                    help="Use ASCII-only box glyphs (for terminals without Unicode).")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    global _G
    args = _parse_args(argv)
    if args.slm and args.offline:
        print("error: pass at most one of --slm / --offline", file=sys.stderr)
        return 2
    if args.only is not None and not (1 <= args.only <= len(EXAMPLES)):
        print("error: --only must be between 1 and {0}".format(len(EXAMPLES)), file=sys.stderr)
        return 2

    # Auto-load .env (cwd, then this repo's root) via prpt's own loader so --slm
    # works with a key on disk — no shell export. The loader strips curly quotes
    # and never overrides an existing shell value.
    for _env_dir in (Path.cwd(), Path(_REPO_ROOT)):
        load_dotenv(_env_dir / ".env")

    _reconfigure_stdout_utf8()
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    _G = Glyphs(unicode_ok=not args.ascii and "utf" in enc)
    p = Palette(_color_enabled(args.no_color))

    print()
    print(" {0}PromptPilot{1} {2} SLM control layer for AI coding agents".format(
        p.bold + p.cyan, p.reset, _G.dash))
    print(" {0}The small model shapes the request; the frontier model still writes the code.{1}"
          .format(p.grey, p.reset))
    print()

    choice = _resolve_normalizer_choice(args, p)
    normalizer, choice = _build_normalizer(choice, args, p)

    examples = EXAMPLES if args.only is None else [EXAMPLES[args.only - 1]]

    # Show the full structured prompt when explicitly asked (--full) or when a
    # single example is isolated (--only) — otherwise only example 1 shows it,
    # to keep a full 3-example run scannable.
    show_full = args.full or args.only is not None
    for i, ex in enumerate(examples, start=1):
        display_idx = args.only if args.only is not None else i
        _render_example(display_idx, len(EXAMPLES), ex, normalizer, p, show_full)

    print(_rule(p))
    print(" {0}Takeaway{1}  constraints pinned, protected spans surfaced, ambiguity flagged {2}"
          .format(p.bold, p.reset, _G.dash))
    print("          {0}before{1} the expensive model spends a single token coding."
          .format(p.bold, p.reset))
    if choice == "heuristic" and not args.offline and not _detect_slm_backend():
        print(" {0}Tip{1}       the live SLM rewrite is sharper. Add {2}--slm{3} with an API key "
              "or subscription.".format(p.dim, p.reset, p.bold, p.reset))
    print(_rule(p))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
