#!/usr/bin/env python3
"""Generate docs/assets/demo.svg — a static before/after demo poster.

This renders the real offline output of examples/demo.py (example 1) into a
self-contained, dependency-free SVG that GitHub will display inline. It is the
zero-setup hero visual for the README and examples page; replace it with a
recorded animated GIF when you have one (see docs/RECORDING.md).

    python scripts/make_demo_svg.py

Nothing is hardcoded: the route, task type, confidence, protected spans, hard
constraints, and final downstream prompt preview are computed by prpt's real
pipeline against example 1's synthetic repo, so the poster can't silently drift
from the tool's behavior. GitHub sanitizes <style>/<script>/animation out of
<img>-embedded SVG, so this uses only plain SVG primitives with inline fills.
"""
from __future__ import annotations

import os
import sys
import textwrap
from typing import Iterable, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from examples.demo import EXAMPLES  # noqa: E402
from prpt.normalizers.base import build_final_downstream_prompt, create_normalizer  # noqa: E402

# --- palette (GitHub dark + PromptPilot accent) ----------------------------
BG = "#0d1117"
PANEL = "#161b22"
PANEL_2 = "#0f172a"
BORDER = "#30363d"
BORDER_ACCENT = "#2563eb"
FG = "#c9d1d9"
DIM = "#8b949e"
MUTED = "#6e7681"
CYAN = "#56d4dd"
BLUE = "#58a6ff"
MAGENTA = "#d2a8ff"
GREEN = "#7ee787"
YELLOW = "#e3b341"
RED = "#ff7b72"
ORANGE = "#ffa657"

# --- geometry --------------------------------------------------------------
WIDTH = 1040
HEIGHT = 710
PAD = 32
CARD_GAP = 24
CARD_W = (WIDTH - PAD * 2 - CARD_GAP) // 2
TOP = 86
LINE_H = 22
FONT = 14
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SANS = "Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif"

Segment = Tuple[str, str]  # (text, color)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text: str, width: int) -> List[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [""]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _normalize_hero():
    ex = EXAMPLES[0]
    norm = create_normalizer("heuristic", load_repo_content=False).normalize(
        ex.prompt, ex.repo, high_stakes=False
    )
    downstream_prompt = build_final_downstream_prompt(norm, ex.repo)
    return ex, norm, downstream_prompt


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


def _bullet_lines(label: str, items: Iterable[str], *, width: int, limit: int) -> List[List[Segment]]:
    lines: List[List[Segment]] = [[(label, CYAN)]]
    shown = list(items)[:limit]
    if not shown:
        lines.append([("  • ", MUTED), ("(none)", DIM)])
        return lines
    for item in shown:
        wrapped = _wrap(item, width - 4)
        lines.append([("  • ", GREEN), (wrapped[0], FG)])
        for cont in wrapped[1:2]:
            lines.append([("    ", GREEN), (cont, FG)])
    return lines


def _prompt_preview_lines(prompt: str, width: int, max_lines: int) -> List[str]:
    lines = _wrap(prompt, width)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [_truncate(lines[max_lines - 1], width - 1)]
    return lines


def _extract_forwarded_preview(downstream_prompt: str, width: int, max_lines: int) -> List[List[Segment]]:
    interesting: List[str] = []
    for raw in downstream_prompt.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(("Original user request", "Hard constraints", "Protected spans", "Suggested approach")):
            interesting.append(stripped)
        elif interesting and len(interesting) < max_lines:
            interesting.append(stripped)
        if len(interesting) >= max_lines:
            break
    if not interesting:
        interesting = downstream_prompt.splitlines()[:max_lines]

    lines: List[List[Segment]] = []
    for raw in interesting[:max_lines]:
        color = BLUE if raw.endswith(":") else FG
        for i, wrapped in enumerate(_wrap(raw, width)):
            prefix = "" if i == 0 else "  "
            lines.append([(prefix + wrapped, color if i == 0 else DIM)])
            if len(lines) >= max_lines:
                return lines
    return lines


def _render() -> str:
    ex, norm, downstream_prompt = _normalize_hero()
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
        "Rough request in → constraint-pinned coding-agent brief out",
        color=DIM,
        size=14,
        family=SANS,
    )
    _pill(out, WIDTH - PAD - 128, 24, "zero setup", GREEN, 128)
    _pill(out, WIDTH - PAD - 270, 24, "offline demo", BLUE, 130)

    card_h = 550
    left_x = PAD
    right_x = PAD + CARD_W + CARD_GAP
    _card(out, left_x, TOP, CARD_W, card_h, "Before", "A normal run-on developer prompt", ORANGE)
    _card(out, right_x, TOP, CARD_W, card_h, "After PromptPilot", "A routed brief with constraints pinned first", GREEN)

    # Arrow connector.
    mid_y = TOP + 250
    out.append(f'<line x1="{left_x + CARD_W + 6}" y1="{mid_y}" x2="{right_x - 6}" y2="{mid_y}" stroke="{BLUE}" stroke-width="3"/>')
    out.append(f'<path d="M {right_x - 10} {mid_y - 7} L {right_x + 2} {mid_y} L {right_x - 10} {mid_y + 7}" fill="none" stroke="{BLUE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')

    # Left card body.
    x = left_x + 22
    y = TOP + 96
    _add_segments(out, x, y, [("$ ", GREEN), ("python examples/demo.py", FG)])
    y += 46
    _add_text(out, x, y, "RAW PROMPT", color=CYAN, size=13, family=SANS, weight="800")
    y += 26
    for line in _prompt_preview_lines(ex.prompt, 52, 8):
        _add_text(out, x, y, line, color=FG, size=14, family=MONO)
        y += LINE_H
    y += 24
    _add_text(out, x, y, "What the agent otherwise has to infer", color=DIM, size=13, family=SANS)
    y += 30
    for line in [
        "Which facts are hard constraints?",
        "Which symbols / paths must survive?",
        "Should it ask, answer, pass through, or act?",
    ]:
        _add_segments(out, x, y, [("? ", YELLOW), (line, FG)])
        y += LINE_H

    # Right card body.
    x = right_x + 22
    y = TOP + 96
    _add_segments(
        out,
        x,
        y,
        [
            ("route=", DIM),
            ("act", MAGENTA),
            ("  task=", DIM),
            (norm.task_type, MAGENTA),
            ("  confidence=", DIM),
            (norm.confidence, MAGENTA),
        ],
    )
    y += 42
    for segments in _bullet_lines("PROTECTED SPANS", norm.protected_spans, width=48, limit=4):
        _add_segments(out, x, y, segments)
        y += LINE_H
    y += 12
    for segments in _bullet_lines("HARD CONSTRAINTS", norm.hard_constraints, width=48, limit=3):
        _add_segments(out, x, y, segments)
        y += LINE_H
    y += 16
    _add_text(out, x, y, "FORWARDED BRIEF PREVIEW", color=CYAN, size=13, family=SANS, weight="800")
    y += 24
    preview_x = x
    preview_w = CARD_W - 44
    preview_h = 118
    out.append(
        f'<rect x="{preview_x - 10}" y="{y - 18}" width="{preview_w}" height="{preview_h}" rx="10" '
        f'fill="{PANEL_2}" stroke="{BORDER_ACCENT}" stroke-opacity="0.45"/>'
    )
    for segments in _extract_forwarded_preview(downstream_prompt, 50, 5):
        _add_segments(out, preview_x, y, segments, size=13)
        y += 20

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
            ("Clarifies ambiguity, preserves APIs / paths, and bounds context before the expensive coding agent runs.", FG),
        ],
        size=14,
        family=SANS,
    )

    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    svg = _render()
    out_path = os.path.join(_REPO_ROOT, "docs", "assets", "demo.svg")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote {0} ({1} bytes)".format(os.path.relpath(out_path, _REPO_ROOT), len(svg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
