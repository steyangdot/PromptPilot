#!/usr/bin/env python3
"""Generate docs/assets/demo.svg — a static before/after demo poster.

This renders the real offline output of examples/demo.py (example 1) into a
self-contained, dependency-free SVG that GitHub will display inline. It is the
zero-setup hero visual for the README and examples page; replace it with a
recorded animated GIF when you have one (see docs/RECORDING.md).

    python scripts/make_demo_svg.py

Nothing is hardcoded: the route, task type, confidence, and the entire "after"
panel are parsed straight out of the structured brief that prpt's real pipeline
forwards to the coding agent (build_final_downstream_prompt against example 1's
synthetic repo), so the poster can't silently drift from the tool's behavior.
The "after" card shows the genuine rewrite — objective, pinned hard constraints,
protected spans, and the downstream instruction — not a paraphrase of the input.
GitHub sanitizes <style>/<script>/animation out of <img>-embedded SVG, so this
uses only plain SVG primitives with inline fills.
"""
from __future__ import annotations

import os
import sys
import textwrap
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from examples.demo import EXAMPLES, _route_of  # noqa: E402
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
HEIGHT = 690
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


def _wrap_capped(text: str, width: int, max_lines: int) -> List[str]:
    """Wrap to at most ``max_lines`` lines, ellipsizing the overflow.

    Unlike a plain ``_wrap(...)[:max_lines]`` this never silently drops text:
    everything past the budget is folded into the final line with a trailing
    ellipsis so a clipped constraint reads as clipped, not as complete.
    """
    lines = _wrap(text, width)
    if len(lines) <= max_lines:
        return lines
    head = lines[: max_lines - 1]
    head.append(_truncate(" ".join(lines[max_lines - 1:]), width))
    return head


def _normalize_hero():
    ex = EXAMPLES[0]
    normalizer = create_normalizer("heuristic", load_repo_content=False)
    norm = normalizer.normalize(ex.prompt, ex.repo, high_stakes=False)
    downstream_prompt = build_final_downstream_prompt(norm, ex.repo)
    route = _route_of(normalizer)
    return ex, norm, downstream_prompt, route


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


def _structured_sections(text: str) -> Tuple[List[str], Dict[str, List[str]]]:
    """Parse the forwarded structured prompt into ordered ``title -> items``.

    A *header* is any line ending in ``:`` that is not itself a bullet
    (``Objective:``, ``Hard constraints:``, ...). Subsequent non-empty lines are
    collected under the most recent header, with a leading ``- `` bullet marker
    stripped. We parse the *actual* downstream prompt so the "after" panel shows
    exactly what prpt forwards, and can't drift from build_structured_prompt.
    """
    order: List[str] = []
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and not stripped.startswith("- "):
            current = stripped[:-1]
            if current not in sections:
                sections[current] = []
                order.append(current)
        elif current is not None:
            sections[current].append(stripped[2:] if stripped.startswith("- ") else stripped)
    return order, sections


def _section_lines(
    title: str,
    items: Sequence[str],
    *,
    mode: str,
    width: int,
    limit: int = 3,
) -> List[List[Segment]]:
    """Render one section of the forwarded brief as colored text lines.

    ``mode`` is one of:
      * ``"para"``   — join items into a wrapped paragraph (Objective, the
                       downstream instruction).
      * ``"bullets"`` — one ``•`` bullet per item, capped at ``limit`` with a
                       ``(+N more)`` overflow marker.
      * ``"inline"`` — items joined onto a compact wrapped line (protected
                       spans, which would otherwise be many one-word bullets).
    """
    lines: List[List[Segment]] = [[(title + ":", CYAN)]]
    items = [it for it in items if it]
    if not items:
        lines.append([("  ", DIM), ("(none)", DIM)])
        return lines
    if mode == "para":
        for w in _wrap_capped(" ".join(items), width, 2):
            lines.append([("  ", FG), (w, FG)])
        return lines
    if mode == "inline":
        for w in _wrap_capped("  ".join(items), width, 2):
            lines.append([("  ", DIM), (w, FG)])
        return lines
    shown = list(items)[:limit]
    for it in shown:
        wrapped = _wrap_capped(it, width - 4, 2)
        lines.append([("  • ", GREEN), (wrapped[0], FG)])
        for cont in wrapped[1:]:
            lines.append([("    ", GREEN), (cont, FG)])
    extra = len(items) - len(shown)
    if extra > 0:
        lines.append([("  ", DIM), (f"(+{extra} more)", DIM)])
    return lines


def _render() -> str:
    ex, norm, downstream_prompt, route = _normalize_hero()
    order, sections = _structured_sections(downstream_prompt)

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

    card_h = 500
    left_x = PAD
    right_x = PAD + CARD_W + CARD_GAP
    _card(out, left_x, TOP, CARD_W, card_h, "Before", "The raw prompt, as typed", ORANGE)
    _card(out, right_x, TOP, CARD_W, card_h, "After PromptPilot", "The structured brief forwarded to the agent", GREEN)

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

    # Right card body — the real structured brief that prpt forwards.
    x = right_x + 22
    y = TOP + 96
    _add_segments(
        out,
        x,
        y,
        [
            ("route=", DIM),
            (route, MAGENTA),
            ("  task=", DIM),
            (norm.task_type, MAGENTA),
            ("  confidence=", DIM),
            (norm.confidence, MAGENTA),
        ],
    )
    y += 28
    _add_text(out, x, y, "▼ forwarded to the coding agent (structured brief)", color=DIM, size=13, family=SANS)
    y += 30

    inner = 50  # mono chars that fit the card width at 14px

    def emit(section_lines: List[List[Segment]]) -> None:
        nonlocal y
        for seg in section_lines:
            _add_segments(out, x, y, seg)
            y += LINE_H
        y += 8

    if "Objective" in sections:
        emit(_section_lines("Objective", sections["Objective"], mode="para", width=inner))
    if "Hard constraints" in sections:
        emit(_section_lines("Hard constraints", sections["Hard constraints"], mode="bullets", width=inner, limit=3))
    if "Protected spans" in sections:
        emit(_section_lines("Protected spans", sections["Protected spans"], mode="inline", width=inner))
    if "Instruction to downstream model" in sections:
        emit(_section_lines("Instruction to downstream model", sections["Instruction to downstream model"], mode="para", width=inner))

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
            ("Same request, re-expressed as a labeled brief — objective, pinned constraints, and protected spans — before the expensive coding agent runs.", FG),
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
