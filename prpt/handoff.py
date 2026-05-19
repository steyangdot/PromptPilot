"""Session handoff round-trip: snapshot to markdown, restore from markdown.

Two operations:

- ``write_handoff(cwd, out_path, clear_after)`` — read the promptpilot session
  for ``cwd`` (full transcript, no TTL filter), ask Haiku-via-Max to write
  a structured handoff.md with five fixed sections (Goal, Decisions made,
  Files touched, Open items, Constraints), write to ``out_path``. Optional
  ``clear_after`` deletes the session afterward; default is False (most
  users keep working in the same session and just want the doc).

- ``read_handoff_into_session(cwd, in_path, append)`` — parse a handoff.md
  with the strict five-section template (regex, no SLM call -- avoids a
  hallucination point), synthesize a single user+assistant pair, append
  to the promptpilot session. Default replaces existing session; ``append=True``
  adds without clearing.

Provider-agnostic: uses ``get_default_judge()`` which auto-picks Max OAuth,
Anthropic API key, or OpenAI API key, in that preference order. Override
with ``PROMPTPILOT_JUDGE=max|anthropic|openai``. This means the handoff workflow
works for codex/OpenAI users without any Anthropic auth (and vice versa).

Tradeoffs (documented in session 2026-05-06_summary):
- Lossy by design: what the user implied but didn't say is gone.
- Synth turns bypass the ORGANIC_TURN_TRUNC cap in load_recent_turns
  (they're written via append_synth_turn with synth=True), so the full
  compressed state survives into the next session's prompt context.
  Organic turns still truncate at the original 300-char limit.
- No verification that bootstrap's parse matches the original markdown.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from prpt.judges import get_default_judge
from prpt.session import load_all_turns, append_synth_turn, clear_session


SECTION_HEADERS = ("Goal", "Decisions made", "Files touched", "Open items", "Constraints")

# Accepted variants per canonical header. Matched case-insensitively, with
# trailing colons/asterisks stripped. Lets users hand-edit handoff.md without
# tripping the strict template (e.g. "Files modified" instead of "Files
# touched", "Decisions" instead of "Decisions made"). The canonical names are
# what gets stored in the parsed section dict.
_HEADER_VARIANTS = {
    "Goal":           ("goal", "goals", "objective", "summary"),
    "Decisions made": ("decisions made", "decisions", "decisions taken"),
    "Files touched":  ("files touched", "files modified", "files changed",
                       "files", "modified files"),
    "Open items":     ("open items", "open", "next steps", "todo", "remaining"),
    "Constraints":    ("constraints", "guardrails", "non-goals", "do not",
                       "do not change"),
}

# Match any `## <header>` line; canonicalize the captured text downstream.
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _canonicalize_header(text: str) -> Optional[str]:
    """Return canonical header name for a raw header text, or None if no match.

    Strips trailing punctuation (`:`, `*`, whitespace) and lowercases before
    matching against the variant list. Returns the canonical name from
    SECTION_HEADERS, preserving the exact casing the rest of the code expects.
    """
    cleaned = text.strip().rstrip(":*").strip().lower()
    for canonical, variants in _HEADER_VARIANTS.items():
        if cleaned in variants:
            return canonical
    return None


_CHECKPOINT_PROMPT = """You are summarizing a coding-agent conversation into a structured handoff document.

Below is the full session transcript (user requests and assistant responses). Write a markdown handoff document with EXACTLY these five section headers, in this order:

## Goal
## Decisions made
## Files touched
## Open items
## Constraints

Each section starts with `## ` (two hash signs and a space). Below each header, write the relevant content as bullet points or short paragraphs.

Section semantics:
- **Goal**: one sentence describing what the user is trying to accomplish.
- **Decisions made**: bullet points of choices the user or assistant committed to (e.g. "use exponential backoff", "skip tests for now").
- **Files touched**: bullet points of file paths that were modified, ideally with line numbers when known. Format: `- path/to/file.py — what changed`. If unknown, omit.
- **Open items**: bullet points of unresolved questions or planned next steps.
- **Constraints**: bullet points of things the user said NOT to do, coding style observed, or other guardrails.

If a section has no content, write a single line under it: `(none)`.

Output ONLY the markdown -- no preamble, no surrounding code fences, no commentary after.

=== SESSION TRANSCRIPT ===
{transcript}
=== END TRANSCRIPT ===
"""


def _format_transcript(turns: list[dict]) -> str:
    """Format session turns for the Haiku checkpoint prompt."""
    if not turns:
        return "(empty session)"
    lines: list[str] = []
    for i, t in enumerate(turns, 1):
        role = t["role"].upper()
        # Don't truncate -- Haiku context is ample, and checkpoint quality
        # depends on having full content. (Bootstrap output goes through
        # the 300-char truncation in load_recent_turns, but checkpoint input
        # does not.)
        lines.append(f"--- Turn {i} ({role}) ---")
        lines.append(t["content"])
    return "\n".join(lines)


def write_handoff(cwd: str, out_path: Path, clear_after: bool = False) -> dict:
    """Read promptpilot session for ``cwd``, write structured handoff.md to ``out_path``.

    Returns a dict with keys ``cost_usd``, ``walltime_s``, ``turns_summarized``,
    ``out_path`` (str), ``cleared`` (bool). On Haiku failure (timeout, parse
    error), raises RuntimeError -- the user can retry.
    """
    turns = load_all_turns(cwd)
    if not turns:
        raise RuntimeError(
            f"No session found for {cwd}. Nothing to checkpoint. "
            "(Was the session cleared? Did promptpilot ever run in this dir?)"
        )

    transcript = _format_transcript(turns)
    prompt = _CHECKPOINT_PROMPT.format(transcript=transcript)
    judge = get_default_judge()
    text, cost, walltime = judge(prompt, timeout=120)

    if not text or not text.strip():
        raise RuntimeError(
            f"Judge ({judge.name}) checkpoint call returned empty -- timeout, "
            "auth failure, or empty session. Verify auth: "
            "`claude auth status` (max), or that ANTHROPIC_API_KEY / "
            "OPENAI_API_KEY is set for SDK-based judges."
        )

    # Validate that all five canonical sections are present (variants OK).
    found = {_canonicalize_header(m) for m in _SECTION_RE.findall(text)}
    found.discard(None)
    missing = [h for h in SECTION_HEADERS if h not in found]
    if missing:
        raise RuntimeError(
            f"Handoff output missing required sections: {missing}. "
            f"Got canonical headers: {sorted(h for h in found if h)}. "
            f"Re-run, or inspect output:\n{text[:500]}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    cleared = False
    if clear_after:
        clear_session(cwd)
        cleared = True

    return {
        "cost_usd": cost,
        "walltime_s": walltime,
        "turns_summarized": len(turns),
        "out_path": str(out_path),
        "cleared": cleared,
    }


def _parse_handoff(text: str) -> dict:
    """Parse a handoff.md into a dict of canonical section name → body text.

    Accepts header variants (case-insensitive, common synonyms — see
    _HEADER_VARIANTS) so hand-edited handoffs survive minor renaming. All
    five canonical SECTION_HEADERS must still be present (else raises). Body
    text "(none)" is stored as an empty string.
    """
    matches = list(_SECTION_RE.finditer(text))
    canonical_pairs = []  # list of (canonical_name, match) preserving order
    for m in matches:
        canonical = _canonicalize_header(m.group(1))
        if canonical is not None:
            canonical_pairs.append((canonical, m))

    found = {name for name, _ in canonical_pairs}
    missing = [h for h in SECTION_HEADERS if h not in found]
    if missing:
        # Show the raw headers we found alongside what's missing so users can
        # see whether their rename simply doesn't match any variant.
        raw = [m.group(1).strip() for m in matches]
        raise RuntimeError(
            f"handoff.md missing required sections: {missing}. "
            f"Found these headers (canonicalized variants OK): {raw}. "
            f"Expected canonical: {list(SECTION_HEADERS)}"
        )

    sections: dict = {}
    for i, (name, m) in enumerate(canonical_pairs):
        body_start = m.end()
        body_end = canonical_pairs[i + 1][1].start() if i + 1 < len(canonical_pairs) else len(text)
        body = text[body_start:body_end].strip()
        if body == "(none)":
            body = ""
        sections[name] = body
    return sections


def _synthesize_pair(sections: dict[str, str]) -> tuple[str, str]:
    """Build a (user_msg, assistant_msg) pair from parsed sections.

    Both messages are written tight: ``load_recent_turns`` truncates to 300
    chars when surfacing in subsequent turns' session-context. The first
    300 chars of each message are what the agent will actually see.
    """
    goal = sections.get("Goal", "").strip() or "(continuing prior work)"
    constraints = sections.get("Constraints", "").strip()
    decisions = sections.get("Decisions made", "").strip()
    files = sections.get("Files touched", "").strip()
    open_items = sections.get("Open items", "").strip()

    # USER: the original task + constraints (front-loaded for the 300-char window)
    user_parts = [f"[resumed from handoff] Goal: {goal}"]
    if constraints:
        user_parts.append(f"Constraints: {constraints}")
    user_msg = "\n".join(user_parts)

    # ASSISTANT: what's already done + what's open (front-load files + open)
    asst_parts = []
    if decisions:
        asst_parts.append(f"Decisions: {decisions}")
    if files:
        asst_parts.append(f"Files modified: {files}")
    if open_items:
        asst_parts.append(f"Open: {open_items}")
    if not asst_parts:
        asst_parts.append("(nothing recorded yet)")
    asst_msg = "\n".join(asst_parts)

    return user_msg, asst_msg


def restart_session(cwd: str, out_path: Path) -> dict:
    """One-shot: checkpoint the current session to ``out_path``, then bootstrap
    a fresh session from it. Equivalent to checkpoint + clear + bootstrap, but
    typed as a single command. The handoff.md is left on disk so the user can
    inspect/recover from it if needed.

    Returns a merged dict with ``cost_usd``, ``walltime_s``, ``turns_summarized``,
    ``out_path``, ``user_msg_chars``, ``assistant_msg_chars``.
    """
    cp = write_handoff(cwd, out_path, clear_after=False)
    bs = read_handoff_into_session(cwd, out_path, append=False)
    return {
        "cost_usd": cp["cost_usd"],
        "walltime_s": cp["walltime_s"],
        "turns_summarized": cp["turns_summarized"],
        "out_path": cp["out_path"],
        "user_msg_chars": bs["user_msg_chars"],
        "assistant_msg_chars": bs["assistant_msg_chars"],
    }


def read_handoff_into_session(cwd: str, in_path: Path, append: bool = False) -> dict:
    """Parse handoff.md at ``in_path``, append synthesized pair to promptpilot session.

    With ``append=False`` (default), the existing session is cleared first --
    bootstrap is treated as a fresh start. With ``append=True``, the synthetic
    pair is added to whatever's already there.

    Returns dict with ``cleared``, ``user_msg_chars``, ``assistant_msg_chars``,
    ``in_path``.
    """
    if not in_path.exists():
        raise RuntimeError(f"Handoff file not found: {in_path}")

    text = in_path.read_text(encoding="utf-8")
    sections = _parse_handoff(text)
    user_msg, asst_msg = _synthesize_pair(sections)

    cleared = False
    if not append:
        clear_session(cwd)
        cleared = True

    append_synth_turn(cwd, "user", user_msg)
    append_synth_turn(cwd, "assistant", asst_msg)

    return {
        "cleared": cleared,
        "user_msg_chars": len(user_msg),
        "assistant_msg_chars": len(asst_msg),
        "in_path": str(in_path),
    }
