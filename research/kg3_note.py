"""KG-3 note construction — a dedicated one-sentence triage diagnosis, appended below evidence.

Design (docs/PHASE3_PRPT_CI_DESIGN.md; parked KG-3 in CAMPAIGN §6). The KG-1 v2 rewrite KILLed
because REPLACING evidence destroyed the verification anchor
(docs/FINDING_REWRITE_TOKEN_ECONOMICS.md). KG-3 keeps the verbatim traceback + the pin in both
arms and, in arm B, APPENDS a tightened diagnosis note.

Why a dedicated diagnostician call (not the slm-openai-v2 rewrite's downstream_prompt): the
dry-run showed the rewrite's first sentence is boilerplate ("Fix the failing test X") — the real
mechanism sits two paragraphs down. A purpose-built nano call yields the diagnosis directly and
is trivially pre-registerable (the prompt below IS the frozen rule).

Frozen note-construction rules (no post-hoc tuning):
  * ONE nano call (gpt-5.4-nano) → strict JSON {mechanism: <=1 sentence root cause in the
    SOURCE, never a test-name restatement>, file: <single most likely httpx/ path or "">}.
  * The note = hedged header + the mechanism line + (the file line ONLY if `file` is a non-test
    httpx/ path that EXISTS at HEAD — the grep-verify; at most one file, so a broad/low-
    confidence guess contributes only the mechanism).
  * empty note (→ arm B degrades to arm A) on call failure or empty mechanism.
The model sees the evidence + the source-file list only (committed-seed clean tree, no diff leak).
"""
from __future__ import annotations

import json
from pathlib import Path

_HEADER = ("[Likely cause — a quick automated triage hypothesis. Verify it against the failing "
           "test output above; if it does not fit, ignore it and follow the traceback.]")

_SYSTEM = (
    "You triage a single failing CI test for a Python library. You are given the failing pytest "
    "output and the list of library SOURCE files. Respond with STRICT JSON and nothing else: "
    '{"mechanism": "<at most one sentence naming the most likely ROOT-CAUSE bug in the library '
    'source — the wrong behavior and where (function/class), NOT a restatement of the test '
    'name>", "file": "<the single most likely source file path taken verbatim from the list, or '
    'an empty string if unsure>"}. Be terse and concrete. Do not propose fixes or steps.')


def _source_files(cwd: str) -> list:
    root = Path(cwd)
    out = []
    for p in sorted(root.glob("httpx/**/*.py")):
        rel = p.relative_to(root).as_posix()
        if "test" not in rel:
            out.append(rel)
    return out


def build_note(evidence: str, cwd: str, verify_fn, model: str | None = None) -> dict:
    """Return dict(note, mechanism, file, raw, ok). `verify_fn(relpath)->bool` reports whether
    the path exists at HEAD. The caller owns any SLM usage tap (this just makes the call)."""
    result = dict(note="", mechanism="", file=None, raw="", ok=False)
    try:
        from openai import OpenAI
        from prpt.core.constants import DEFAULT_SLM_OPENAI
    except Exception as e:
        result["raw"] = "import-failed: {0}".format(e)
        return result
    mdl = model or DEFAULT_SLM_OPENAI
    srcs = _source_files(cwd)
    user = "Source files:\n{0}\n\nFailing test output:\n{1}".format(
        "\n".join(srcs), evidence.strip()[-3500:])
    try:
        resp = OpenAI().chat.completions.create(
            model=mdl, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user}])
        raw = resp.choices[0].message.content or ""
        result["raw"] = raw
        obj = json.loads(raw)
        if not isinstance(obj, dict):        # json_object mode should guarantee a dict; fail-open
            result["raw"] += " | non-dict json"
            return result
    except Exception as e:
        result["raw"] = (result["raw"] or "") + " | call/parse-failed: {0}".format(e)
        return result

    mech = " ".join(str(obj.get("mechanism", "")).split())[:220]
    fpath = str(obj.get("file", "")).replace("\\", "/").strip()
    result.update(mechanism=mech, ok=True)

    # File line ONLY when there is a real mechanism (never a bare pointer with no diagnosis) AND
    # the path is an httpx/ non-test source file existing at HEAD (grep-verify; reject `..`
    # traversal in our own check, not just via git's tree-spec rejection).
    top = None
    name = fpath.rsplit("/", 1)[-1]
    if (mech and fpath.startswith("httpx/") and ".." not in fpath and name.endswith(".py")
            and "test" not in name and verify_fn(fpath)):
        top = fpath
    result["file"] = top

    lines = [_HEADER]
    if mech:
        lines.append("- Suspected mechanism: " + mech)
    if top:
        lines.append("- Suspected source file: " + top + " (one candidate; not authoritative)")
    # note requires a mechanism (top implies mech), so len>1 == "has a diagnosis".
    result["note"] = "\n".join(lines) if len(lines) > 1 else ""
    return result
