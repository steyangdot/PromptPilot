#!/usr/bin/env python3
"""Generate docs/assets/demo.svg — a static terminal "poster" of the demo.

This renders the real offline output of examples/demo.py (example 1) into a
self-contained, dependency-free SVG that GitHub will display inline. It is the
zero-setup placeholder / fallback for the README; replace it with a recorded
animated GIF when you have one (see docs/RECORDING.md).

    python scripts/make_demo_svg.py

Nothing is hardcoded: the route, task type, confidence, protected spans, and
hard constraints are computed by prpt's real pipeline against example 1's
synthetic repo, so the poster can't silently drift from the tool's behavior.
GitHub sanitizes <style>/<script>/animation out of <img>-embedded SVG, so this
uses only plain <rect>/<text> with fill attributes — which always renders.
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from examples.demo import EXAMPLES  # noqa: E402
from prpt.normalizers.base import build_final_downstream_prompt, create_normalizer  # noqa: E402

# --- palette (GitHub dark) -------------------------------------------------
BG = "#0d1117"
BAR = "#161b22"
BORDER = "#30363d"
FG = "#c9d1d9"
DIM = "#8b949e"
CYAN = "#56d4dd"
MAGENTA = "#d2a8ff"
GREEN = "#7ee787"
YELLOW = "#e3b341"
RED = "#ff7b72"

# --- geometry --------------------------------------------------------------
PAD_X = 22
BAR_H = 34
TOP = BAR_H + 22
LINE_H = 23
FONT = 14
CHAR_W = 8.4              # monospace advance at 14px
WRAP = 78                 # wrap budget in chars
WIDTH = int(PAD_X * 2 + WRAP * CHAR_W) + 8

Segment = Tuple[str, str]   # (text, color)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text: str, width: int = WRAP) -> List[str]:
    import textwrap
    return textwrap.wrap(text, width=width) or [""]


def _normalize_hero():
    ex = EXAMPLES[0]
    norm = create_normalizer("heuristic", load_repo_content=False).normalize(
        ex.prompt, ex.repo, high_stakes=False)
    build_final_downstream_prompt(norm, ex.repo)  # exercise the real builder
    return ex, norm


def _build_lines(ex, norm) -> List[List[Segment]]:
    """Build the poster as a list of lines; each line is a list of segments."""
    lines: List[List[Segment]] = []

    lines.append([("$ ", GREEN), ("python examples/demo.py", FG)])
    lines.append([])
    lines.append([("RAW PROMPT", CYAN)])
    for w in _wrap(ex.prompt, WRAP - 2):
        lines.append([("  " + w, DIM)])
    lines.append([])
    lines.append([
        ("PROMPTPILOT  ", CYAN),
        ("route=", DIM), ("act", MAGENTA),
        ("  task=", DIM), (norm.task_type, MAGENTA),
        ("  confidence=", DIM), (norm.confidence, MAGENTA),
    ])
    lines.append([])
    lines.append([("EXTRACTED", CYAN)])

    def field(name: str, items: List[str], limit: int = 3) -> None:
        shown = items[:limit]
        if not shown:
            lines.append([("  {0:<18}".format(name), DIM), ("(none)", DIM)])
            return
        for i, it in enumerate(shown):
            label = "  {0:<18}".format(name if i == 0 else "")
            text = it if len(it) <= WRAP - 20 else it[:WRAP - 23] + "..."
            lines.append([(label, DIM), (text, FG)])
        if len(items) > limit:
            lines.append([("  {0:<18}".format(""), DIM),
                          ("(+{0} more)".format(len(items) - limit), DIM)])

    field("protected spans", norm.protected_spans, limit=4)
    field("hard constraints", norm.hard_constraints, limit=3)
    lines.append([])
    lines.append([
        ("> structured prompt forwarded to the coding agent ", GREEN),
        ("(constraints pinned first)", DIM),
    ])
    return lines


def _render(lines: List[List[Segment]]) -> str:
    height = TOP + len(lines) * LINE_H + 18
    out: List[str] = []
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}" font-family="ui-monospace,SFMono-Regular,'
        'Menlo,Consolas,monospace" font-size="{fs}">'.format(w=WIDTH, h=height, fs=FONT))
    # window
    out.append('<rect x="0" y="0" width="{w}" height="{h}" rx="8" fill="{bg}" '
               'stroke="{br}"/>'.format(w=WIDTH, h=height, bg=BG, br=BORDER))
    out.append('<rect x="0" y="0" width="{w}" height="{bh}" rx="8" fill="{bar}"/>'
               .format(w=WIDTH, bh=BAR_H, bar=BAR))
    out.append('<rect x="0" y="{y}" width="{w}" height="10" fill="{bar}"/>'
               .format(y=BAR_H - 10, w=WIDTH, bar=BAR))
    for i, col in enumerate((RED, YELLOW, GREEN)):
        out.append('<circle cx="{cx}" cy="17" r="6" fill="{c}"/>'
                   .format(cx=20 + i * 20, c=col))
    out.append('<text x="{x}" y="22" fill="{dim}" text-anchor="middle">'
               'prpt — PromptPilot demo</text>'.format(x=WIDTH // 2, dim=DIM))
    # body
    for row, segs in enumerate(lines):
        y = TOP + row * LINE_H
        x = PAD_X
        if not segs:
            continue
        parts = []
        for text, color in segs:
            parts.append('<tspan fill="{c}" xml:space="preserve">{t}</tspan>'
                         .format(c=color, t=_esc(text)))
        out.append('<text x="{x}" y="{y}">{spans}</text>'
                   .format(x=x, y=y, spans="".join(parts)))
    out.append('</svg>')
    return "\n".join(out) + "\n"


def main() -> int:
    ex, norm = _normalize_hero()
    svg = _render(_build_lines(ex, norm))
    out_path = os.path.join(_REPO_ROOT, "docs", "assets", "demo.svg")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote {0} ({1} bytes)".format(os.path.relpath(out_path, _REPO_ROOT), len(svg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
