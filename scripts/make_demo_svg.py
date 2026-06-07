#!/usr/bin/env python3
"""Generate docs/assets/demo.svg — a 4-step poster of PromptPilot's clarify flow.

The poster tells the story example 1 could not: a *vague* request is routed to
`clarify` (PromptPilot asks one sharp question instead of guessing), the
developer answers in a line, and PromptPilot then routes `act` and forwards a
precise, constraint-pinned brief to the coding agent.

  step 1  you type        a vague one-liner
  step 2  PromptPilot      route=clarify  -> a scannable clarifying question
  step 3  you answer       one line of detail
  step 4  PromptPilot      route=act      -> the rewritten brief it forwards

Steps 2 and 4 are genuine small-model output from the v2 control plane
(slm-anthropic-v2: the JSON ExecutionSpec normalizer that emits the routing
decision). Because that output needs an API key and is not bit-for-bit
deterministic, the live run is captured once into docs/assets/demo_capture.json
(committed) and the SVG renders from that snapshot — so the poster rebuilds in
CI with no key, no network, and never drifts:

    python scripts/make_demo_svg.py            # render from the committed capture
    python scripts/make_demo_svg.py --live     # re-run the real SLM, refresh both

--live loads ANTHROPIC_API_KEY from the environment or a local .env (via prpt's
own loader) and drives create_normalizer("slm-anthropic-v2") through both turns.
GitHub sanitizes <style>/<script>/animation out of <img>-embedded SVG, so this
uses only plain SVG primitives with inline fills.
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
MUTED = "#6e7681"
CYAN = "#56d4dd"
BLUE = "#58a6ff"
MAGENTA = "#d2a8ff"
GREEN = "#7ee787"
YELLOW = "#e3b341"
ORANGE = "#ffa657"

# --- geometry --------------------------------------------------------------
WIDTH = 1000
PAD = 28
RAIL_X = 52              # centre of the step-number dots
CARD_X = 88
CARD_W = WIDTH - CARD_X - PAD
CARD_PAD = 18
HEAD_H = 30             # header band inside a panel
LINE_H = 22
GAP_H = 16             # blank-line spacing inside a body
STEP_GAP = 34          # vertical connector between panels
WRAP = 92              # mono chars that fit a card row at 14px
FONT = 14
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SANS = "Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif"

Segment = Tuple[str, str]  # (text, color)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text: str, width: int = WRAP) -> List[str]:
    return textwrap.wrap(text, width=width, break_long_words=False) or [""]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


# ---------------------------------------------------------------------------
# Capture: run the real v2 normalizer once, or load the committed snapshot.
# ---------------------------------------------------------------------------

def _capture_live() -> Dict:
    """Drive slm-anthropic-v2 through the vague -> clarify -> answer -> rewrite flow."""
    from pathlib import Path

    from prpt.core.dotenv import load_dotenv

    for cand in (Path.cwd() / ".env", Path(_REPO_ROOT) / ".env"):
        try:
            load_dotenv(cand)
        except Exception:
            pass

    from examples.demo import CLARIFY_EXAMPLE, _route_of
    from prpt.normalizers.base import build_final_downstream_prompt, create_normalizer

    ex = CLARIFY_EXAMPLE
    if not ex.answer:
        raise SystemExit("CLARIFY_EXAMPLE must carry a representative `answer`.")

    norm = create_normalizer("slm-anthropic-v2", load_repo_content=False)

    # Turn 1: the vague prompt -> clarify.
    n1 = norm.normalize(ex.prompt, ex.repo, high_stakes=False)
    route1 = _route_of(norm)
    spec1 = getattr(norm, "_last_spec", None)

    # Turn 2: original + question + the developer's answer -> act (rewrite).
    norm._last_context_block = None
    combined = (
        "Original request: {0}\n\n"
        "Clarifying question asked: {1}\n\n"
        "Developer's answer: {2}"
    ).format(ex.prompt, n1.normalized_prompt, ex.answer)
    n2 = norm.normalize(combined, ex.repo, high_stakes=False)
    route2 = _route_of(norm)
    spec2 = getattr(norm, "_last_spec", None)
    forwarded = build_final_downstream_prompt(n2, ex.repo)

    return {
        "normalizer": type(norm).__name__,
        "raw_prompt": ex.prompt,
        "clarify": {
            "route": route1,
            "question": n1.normalized_prompt,
            "risk": getattr(spec1, "risk", "") or "",
            "memory_record": getattr(spec1, "memory_record", "") or "",
        },
        "answer": ex.answer,
        "rewrite": {
            "route": route2,
            "task_type": n2.task_type,
            "confidence": n2.confidence,
            "target_files": list(getattr(spec2, "target_files", []) or []),
            "memory_record": getattr(spec2, "memory_record", "") or "",
            "downstream_prompt": forwarded,
        },
    }


def _load_capture() -> Dict:
    if not os.path.exists(CAPTURE_PATH):
        raise SystemExit(
            "missing {0}\nRun `python scripts/make_demo_svg.py --live` once (with an "
            "ANTHROPIC_API_KEY / .env) to capture a real clarify->rewrite flow.".format(
                os.path.relpath(CAPTURE_PATH, _REPO_ROOT)
            )
        )
    with open(CAPTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# SVG primitives.
# ---------------------------------------------------------------------------

def _add_text(
    out: List[str], x: int, y: int, text: str, *,
    color: str = FG, size: int = FONT, family: str = MONO,
    weight: Optional[str] = None, anchor: Optional[str] = None,
) -> None:
    attrs = [f'x="{x}"', f'y="{y}"', f'fill="{color}"',
             f'font-size="{size}"', f'font-family="{family}"']
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    out.append(f'<text {" ".join(attrs)}>{_esc(text)}</text>')


def _add_segments(
    out: List[str], x: int, y: int, segments: Sequence[Segment], *,
    size: int = FONT, family: str = MONO,
) -> None:
    spans = "".join(
        '<tspan fill="{0}" xml:space="preserve">{1}</tspan>'.format(c, _esc(t))
        for t, c in segments
    )
    out.append(
        f'<text x="{x}" y="{y}" fill="{FG}" font-size="{size}" '
        f'font-family="{family}">{spans}</text>'
    )


def _pill(out: List[str], x: int, y: int, text: str, color: str, width: int) -> None:
    out.append(
        f'<rect x="{x}" y="{y}" width="{width}" height="24" rx="12" '
        f'fill="{color}" fill-opacity="0.14" stroke="{color}" stroke-opacity="0.55"/>'
    )
    _add_text(out, x + width // 2, y + 16, text, color=color, size=12,
              family=SANS, weight="700", anchor="middle")


# ---------------------------------------------------------------------------
# Body builders — turn capture text into colored, wrapped segment-lines.
# ---------------------------------------------------------------------------

def _plain_lines(text: str, color: str = FG) -> List[List[Segment]]:
    return [[(w, color)] for w in _wrap(text)]


def _clarify_lines(question: str) -> List[List[Segment]]:
    """Render a clarify question: intro in FG, 'A) ...' options with a cyan
    letter, and a trailing 'Also:'/'If' follow-up dimmed."""
    lines: List[List[Segment]] = []
    for raw in question.split("\n"):
        s = raw.strip()
        if not s:
            lines.append([])  # blank -> half-gap
            continue
        if len(s) >= 2 and s[0].isalpha() and s[1] == ")":
            letter, rest = s[:2], s[2:].strip()
            wrapped = _wrap(rest, WRAP - 6)
            lines.append([("  " + letter + " ", CYAN), (wrapped[0], FG)])
            for cont in wrapped[1:]:
                lines.append([("     ", CYAN), (cont, FG)])
        elif s.lower().startswith(("also:", "also ", "if ", "note:")):
            for w in _wrap(s):
                lines.append([(w, DIM)])
        else:
            for w in _wrap(s):
                lines.append([(w, FG)])
    return lines


def _forwarded_lines(downstream_prompt: str) -> List[List[Segment]]:
    """Render the forwarded brief: prose in FG, the trailing [cwd=...] runtime
    context line dimmed."""
    lines: List[List[Segment]] = []
    for raw in downstream_prompt.split("\n"):
        s = raw.strip()
        if not s:
            lines.append([])
            continue
        if s.startswith("[") and s.endswith("]"):
            for w in _wrap(s):
                lines.append([(w, DIM)])
        else:
            for w in _wrap(s):
                lines.append([(w, FG)])
    return lines


# ---------------------------------------------------------------------------
# Panels + timeline.
# ---------------------------------------------------------------------------

def _panel(
    out: List[str], y: int, *,
    num: int, who: str, accent: str,
    body: List[List[Segment]],
    route: Optional[str] = None,
) -> int:
    body_h = sum(LINE_H if segs else GAP_H for segs in body)
    h = CARD_PAD + HEAD_H + body_h + CARD_PAD - 4

    out.append(f'<rect x="{CARD_X}" y="{y}" width="{CARD_W}" height="{h}" rx="14" '
               f'fill="{PANEL}" stroke="{BORDER}"/>')
    out.append(f'<rect x="{CARD_X}" y="{y}" width="5" height="{h}" rx="2.5" fill="{accent}"/>')

    # Step dot on the rail.
    cy = y + 24
    out.append(f'<circle cx="{RAIL_X}" cy="{cy}" r="14" fill="{accent}" fill-opacity="0.16" '
               f'stroke="{accent}" stroke-opacity="0.7"/>')
    _add_text(out, RAIL_X, cy + 5, str(num), color=accent, size=14, family=SANS,
              weight="800", anchor="middle")

    # Header: who + optional route pill + optional meta.
    _add_text(out, CARD_X + CARD_PAD, y + 26, who, color=FG, size=14, family=SANS, weight="700")
    if route:
        pw = 86
        _pill(out, CARD_X + CARD_W - CARD_PAD - pw, y + 11, "route=" + route,
              GREEN if route == "act" else YELLOW, pw)

    # Body.
    yy = y + CARD_PAD + HEAD_H
    for segs in body:
        if not segs:
            yy += GAP_H
            continue
        _add_segments(out, CARD_X + CARD_PAD, yy, segs)
        yy += LINE_H
    return y + h


def _connector(out: List[str], y: int, label: str) -> int:
    midx = RAIL_X
    out.append(f'<line x1="{midx}" y1="{y + 4}" x2="{midx}" y2="{y + STEP_GAP - 4}" '
               f'stroke="{MUTED}" stroke-width="2"/>')
    out.append(f'<path d="M {midx - 5} {y + STEP_GAP - 11} L {midx} {y + STEP_GAP - 4} '
               f'L {midx + 5} {y + STEP_GAP - 11}" fill="none" stroke="{MUTED}" '
               f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    _add_text(out, CARD_X, y + STEP_GAP - 8, label, color=DIM, size=12, family=SANS)
    return y + STEP_GAP


def _render(cap: Dict) -> str:
    clarify = cap["clarify"]
    rewrite = cap["rewrite"]

    body: List[str] = []
    y = 104  # below the header band

    y = _panel(
        body, y, num=1, who="You type", accent=BLUE,
        body=_plain_lines(cap["raw_prompt"]),
    )
    y = _connector(body, y, "PromptPilot routes the request")
    y = _panel(
        body, y, num=2, who="PromptPilot  ·  asks instead of guessing", accent=YELLOW,
        route=clarify.get("route", "clarify"),
        body=_clarify_lines(clarify["question"]),
    )
    y = _connector(body, y, "you answer in one line")
    y = _panel(
        body, y, num=3, who="You answer", accent=BLUE,
        body=_plain_lines(cap["answer"]),
    )
    y = _connector(body, y, "PromptPilot routes the request")
    meta_line: List[Segment] = [
        ("task=", DIM), (rewrite.get("task_type", ""), MAGENTA),
        ("   confidence=", DIM), (rewrite.get("confidence", ""), MAGENTA),
    ]
    body4 = [meta_line, []] + _forwarded_lines(rewrite["downstream_prompt"])
    y = _panel(
        body, y, num=4, who="PromptPilot  ·  forwards a precise brief to the coding agent",
        accent=GREEN, route=rewrite.get("route", "act"),
        body=body4,
    )

    content_bottom = y
    footer_h = 44
    height = content_bottom + 22 + footer_h + PAD

    out: List[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}">'
    )
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{height}" rx="24" fill="{BG}"/>')
    out.append(
        '<defs><linearGradient id="hero" x1="0" x2="1" y1="0" y2="1">'
        '<stop offset="0" stop-color="#1d4ed8" stop-opacity="0.33"/>'
        '<stop offset="0.55" stop-color="#7c3aed" stop-opacity="0.20"/>'
        '<stop offset="1" stop-color="#14b8a6" stop-opacity="0.16"/>'
        '</linearGradient></defs>'
    )
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{height}" rx="24" fill="url(#hero)"/>')

    # Header band.
    _add_text(out, PAD, 44, "PromptPilot visual demo", color=FG, size=26, family=SANS, weight="800")
    _add_text(out, PAD, 72,
              "A vague ask becomes a precise brief — it asks one sharp question first, "
              "instead of burning an agent run on a guess.",
              color=DIM, size=14, family=SANS)
    _pill(out, WIDTH - PAD - 120, 30, "real SLM", GREEN, 120)
    _pill(out, WIDTH - PAD - 120 - 12 - 134, 30, "slm-anthropic-v2", BLUE, 134)

    out.extend(body)

    # Footer outcome strip.
    fy = height - PAD - footer_h
    out.append(f'<rect x="{CARD_X}" y="{fy}" width="{CARD_W}" height="{footer_h}" rx="14" '
               f'fill="#052e2b" fill-opacity="0.85" stroke="{GREEN}" stroke-opacity="0.45"/>')
    _add_segments(out, CARD_X + 20, fy + 27, [
        ("✓ ", GREEN),
        ("A vague ask earns one sharp question — not a wrong guess — then a "
         "constraint-pinned brief, forwarded to the agent once.", FG),
    ], size=14, family=SANS)

    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true",
        help="Re-run the real slm-anthropic-v2 clarify->rewrite flow and refresh "
             "docs/assets/demo_capture.json before rendering.",
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
