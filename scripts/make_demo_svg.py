#!/usr/bin/env python3
"""Generate docs/assets/demo.svg — a before/after poster of a real SLM rewrite.

The "after" panel shows the genuine output of prpt's small-model normalizer
(slm-anthropic) on example 1: the rough, run-on developer prompt rewritten into
a structured, constraint-pinned brief and the exact text that prpt forwards to
the coding agent.

Because the SLM rewrite needs an API key and is not bit-for-bit deterministic,
the live run is captured once into docs/assets/demo_capture.json and committed
alongside the SVG. The default build renders straight from that capture, so the
poster can be regenerated in CI with no key and no network and never drifts:

    python scripts/make_demo_svg.py            # render from the committed capture
    python scripts/make_demo_svg.py --live     # re-run the real SLM, refresh both

--live loads ANTHROPIC_API_KEY from the environment or a local .env (via prpt's
own loader) and calls create_normalizer("slm"). GitHub sanitizes
<style>/<script>/animation out of <img>-embedded SVG, so this uses only plain
SVG primitives with inline fills.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

CAPTURE_PATH = os.path.join(_REPO_ROOT, "docs", "assets", "demo_capture.json")
OUT_PATH = os.path.join(_REPO_ROOT, "docs", "assets", "demo.svg")

# --- palette (GitHub dark + PromptPilot accent) ----------------------------
BG = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
FG = "#c9d1d9"
DIM = "#8b949e"
CYAN = "#56d4dd"
BLUE = "#58a6ff"
MAGENTA = "#d2a8ff"
GREEN = "#7ee787"
YELLOW = "#e3b341"
ORANGE = "#ffa657"

# --- geometry --------------------------------------------------------------
WIDTH = 1040
HEIGHT = 620
PAD = 32
CARD_GAP = 24
CARD_W = (WIDTH - PAD * 2 - CARD_GAP) // 2
CARD_H = 440
TOP = 86
LINE_H = 22
GAP_H = 10            # blank-line spacing inside the forwarded brief
FONT = 14
WRAP = 50            # mono chars that fit a card column at 14px
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SANS = "Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif"

Segment = Tuple[str, str]  # (text, color)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text: str, width: int) -> List[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [""]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _wrap_capped(text: str, width: int, max_lines: int) -> List[str]:
    """Wrap to at most ``max_lines`` lines, ellipsizing the overflow.

    Never silently drops text: everything past the budget is folded into the
    final line with a trailing ellipsis so a clipped line reads as clipped.
    """
    lines = _wrap(text, width)
    if len(lines) <= max_lines:
        return lines
    head = lines[: max_lines - 1]
    head.append(_truncate(" ".join(lines[max_lines - 1:]), width))
    return head


# ---------------------------------------------------------------------------
# Capture: run the real SLM once, or load the committed snapshot.
# ---------------------------------------------------------------------------

def _capture_live() -> Dict[str, str]:
    """Run the real small-model normalizer on example 1 and snapshot its output."""
    from pathlib import Path

    from prpt.core.dotenv import load_dotenv

    for cand in (Path.cwd() / ".env", Path(_REPO_ROOT) / ".env"):
        try:
            load_dotenv(cand)
        except Exception:
            pass

    from examples.demo import EXAMPLES, _route_of
    from prpt.normalizers.base import build_final_downstream_prompt, create_normalizer

    ex = EXAMPLES[0]
    normalizer = create_normalizer("slm", load_repo_content=False)
    norm = normalizer.normalize(ex.prompt, ex.repo, high_stakes=False)
    return {
        "normalizer": type(normalizer).__name__,
        "raw_prompt": ex.prompt,
        "route": _route_of(normalizer),
        "task_type": norm.task_type,
        "confidence": norm.confidence,
        "downstream_prompt": build_final_downstream_prompt(norm, ex.repo),
    }


def _load_capture() -> Dict[str, str]:
    if not os.path.exists(CAPTURE_PATH):
        raise SystemExit(
            "missing {0}\nRun `python scripts/make_demo_svg.py --live` once (with an "
            "ANTHROPIC_API_KEY / .env) to capture a real SLM rewrite.".format(
                os.path.relpath(CAPTURE_PATH, _REPO_ROOT)
            )
        )
    with open(CAPTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# SVG primitives.
# ---------------------------------------------------------------------------

def _add_text(
    out: List[str],
    x: int,
    y: int,
    text: str,
    *,
    color: str = FG,
    size: int = FONT,
    family: str = MONO,
    weight: Optional[str] = None,
    anchor: Optional[str] = None,
) -> None:
    attrs = [
        f'x="{x}"',
        f'y="{y}"',
        f'fill="{color}"',
        f'font-size="{size}"',
        f'font-family="{family}"',
    ]
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    out.append(f'<text {" ".join(attrs)}>{_esc(text)}</text>')


def _add_segments(
    out: List[str],
    x: int,
    y: int,
    segments: Sequence[Segment],
    *,
    size: int = FONT,
    family: str = MONO,
) -> None:
    spans = "".join(
        '<tspan fill="{color}" xml:space="preserve">{text}</tspan>'.format(
            color=color, text=_esc(text)
        )
        for text, color in segments
    )
    out.append(
        f'<text x="{x}" y="{y}" fill="{FG}" font-size="{size}" '
        f'font-family="{family}">{spans}</text>'
    )


def _card(out: List[str], x: int, y: int, w: int, h: int, title: str, subtitle: str, accent: str) -> None:
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" '
        f'fill="{PANEL}" stroke="{BORDER}"/>'
    )
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="5" rx="2.5" fill="{accent}"/>'
    )
    _add_text(out, x + 22, y + 34, title, color=FG, size=18, family=SANS, weight="700")
    _add_text(out, x + 22, y + 58, subtitle, color=DIM, size=13, family=SANS)


def _pill(out: List[str], x: int, y: int, text: str, color: str, width: int) -> None:
    out.append(
        f'<rect x="{x}" y="{y}" width="{width}" height="28" rx="14" '
        f'fill="{color}" fill-opacity="0.13" stroke="{color}" stroke-opacity="0.55"/>'
    )
    _add_text(out, x + width // 2, y + 19, text, color=color, size=12, family=SANS, weight="700", anchor="middle")


def _prompt_preview_lines(prompt: str, width: int, max_lines: int) -> List[str]:
    lines = _wrap(prompt, width)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [_truncate(lines[max_lines - 1], width - 1)]
    return lines


def _render_forwarded(out: List[str], x: int, y: int, downstream_prompt: str, *, max_lines: int = 12) -> int:
    """Render the SLM's forwarded prompt faithfully, line by line.

    Section headers (``Requirements:``) are highlighted, ``- `` bullets become
    ``•`` items, and the trailing ``[cwd=...; ...]`` runtime-context line is
    dimmed. This is the exact text prpt sends downstream — a real rewrite, not a
    paraphrase of the input.
    """
    drawn = 0

    def line(segments: Sequence[Segment], *, size: int = FONT) -> bool:
        nonlocal y, drawn
        if drawn >= max_lines:
            return False
        _add_segments(out, x, y, segments, size=size)
        y += LINE_H
        drawn += 1
        return True

    for raw in downstream_prompt.splitlines():
        stripped = raw.strip()
        if not stripped:
            y += GAP_H
            continue
        if drawn >= max_lines:
            _add_segments(out, x, y, [("…", DIM)])
            y += LINE_H
            break
        if stripped.startswith("[") and stripped.endswith("]"):
            for w in _wrap(stripped, WRAP):
                if not line([(w, DIM)], size=13):
                    break
        elif stripped.endswith(":") and len(stripped.split()) <= 3:
            line([(stripped, CYAN)])
        elif stripped.startswith("- "):
            wrapped = _wrap_capped(stripped[2:], WRAP - 4, 2)
            line([("  • ", GREEN), (wrapped[0], FG)])
            for cont in wrapped[1:]:
                line([("    ", GREEN), (cont, FG)])
        else:
            for w in _wrap(stripped, WRAP):
                if not line([(w, FG)]):
                    break
    return y


def _render(cap: Dict[str, str]) -> str:
    out: List[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">'
    )
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" rx="24" fill="{BG}"/>')
    out.append(
        '<defs>'
        '<linearGradient id="hero" x1="0" x2="1" y1="0" y2="1">'
        '<stop offset="0" stop-color="#1d4ed8" stop-opacity="0.35"/>'
        '<stop offset="0.55" stop-color="#7c3aed" stop-opacity="0.22"/>'
        '<stop offset="1" stop-color="#14b8a6" stop-opacity="0.18"/>'
        '</linearGradient>'
        '</defs>'
    )
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" rx="24" fill="url(#hero)"/>')

    _add_text(out, PAD, 42, "PromptPilot visual demo", color=FG, size=26, family=SANS, weight="800")
    _add_text(
        out,
        PAD,
        68,
        "A real small-model rewrite: rough request in → constraint-pinned agent brief out",
        color=DIM,
        size=14,
        family=SANS,
    )
    _pill(out, WIDTH - PAD - 120, 24, "real SLM", GREEN, 120)
    _pill(out, WIDTH - PAD - 120 - 12 - 134, 24, "slm-anthropic", BLUE, 134)

    left_x = PAD
    right_x = PAD + CARD_W + CARD_GAP
    _card(out, left_x, TOP, CARD_W, CARD_H, "Before", "The raw prompt, as typed", ORANGE)
    _card(out, right_x, TOP, CARD_W, CARD_H, "After PromptPilot", "Rewritten by the small model, forwarded to the agent", GREEN)

    # Arrow connector, vertically centered on the cards.
    mid_y = TOP + CARD_H // 2
    out.append(f'<line x1="{left_x + CARD_W + 6}" y1="{mid_y}" x2="{right_x - 6}" y2="{mid_y}" stroke="{BLUE}" stroke-width="3"/>')
    out.append(f'<path d="M {right_x - 10} {mid_y - 7} L {right_x + 2} {mid_y} L {right_x - 10} {mid_y + 7}" fill="none" stroke="{BLUE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')

    # Left card body — the raw prompt + what the agent would otherwise infer.
    x = left_x + 22
    y = TOP + 96
    _add_segments(out, x, y, [("$ ", GREEN), ("prpt --slm < prompt.txt", FG)])
    y += 46
    _add_text(out, x, y, "RAW PROMPT", color=CYAN, size=13, family=SANS, weight="800")
    y += 26
    for ln in _prompt_preview_lines(cap["raw_prompt"], 52, 8):
        _add_text(out, x, y, ln, color=FG, size=14, family=MONO)
        y += LINE_H
    y += 24
    _add_text(out, x, y, "What the agent otherwise has to infer", color=DIM, size=13, family=SANS)
    y += 30
    for ln in [
        "Which facts are hard constraints?",
        "Which symbols / paths must survive?",
        "Should it ask, answer, pass through, or act?",
    ]:
        _add_segments(out, x, y, [("? ", YELLOW), (ln, FG)])
        y += LINE_H

    # Right card body — the real SLM rewrite that prpt forwards.
    x = right_x + 22
    y = TOP + 96
    _add_segments(
        out,
        x,
        y,
        [
            ("route=", DIM),
            (cap["route"], MAGENTA),
            ("  task=", DIM),
            (cap["task_type"], MAGENTA),
            ("  confidence=", DIM),
            (cap["confidence"], MAGENTA),
        ],
    )
    y += 28
    _add_text(out, x, y, "▼ small-model rewrite, forwarded to the coding agent", color=DIM, size=13, family=SANS)
    y += 28
    _render_forwarded(out, x, y, cap["downstream_prompt"])

    # Footer outcome strip.
    footer_y = HEIGHT - 48
    out.append(
        f'<rect x="{PAD}" y="{footer_y - 22}" width="{WIDTH - PAD * 2}" height="40" rx="20" '
        f'fill="#052e2b" fill-opacity="0.85" stroke="{GREEN}" stroke-opacity="0.45"/>'
    )
    _add_segments(
        out,
        PAD + 24,
        footer_y + 3,
        [
            ("✓ ", GREEN),
            ("One run-on request, rewritten into a structured, constraint-pinned brief before the expensive coding agent runs.", FG),
        ],
        size=14,
        family=SANS,
    )

    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Re-run the real SLM and refresh docs/assets/demo_capture.json before rendering.",
    )
    args = parser.parse_args()

    if args.live:
        cap = _capture_live()
        os.makedirs(os.path.dirname(CAPTURE_PATH), exist_ok=True)
        with open(CAPTURE_PATH, "w", encoding="utf-8") as f:
            json.dump(cap, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("captured {0} via {1}".format(
            os.path.relpath(CAPTURE_PATH, _REPO_ROOT), cap.get("normalizer", "slm")))
    else:
        cap = _load_capture()

    svg = _render(cap)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote {0} ({1} bytes)".format(os.path.relpath(OUT_PATH, _REPO_ROOT), len(svg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
