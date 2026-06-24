"""ProjectState Ledger + FeatureMap + Refactor Guard — the MVP memory system for
PromptPilot's bounded session.  Design: docs/SESSION_MEMORY_ARCHITECTURE.md §5–§7.

WHY: the current bounded session (prpt/session.py `load_recent_turns`) is a recency
WINDOW (last MAX_TURNS=4 pairs, 300-char truncated). On chains longer than ~5 turns,
early-turn CONTRACTS fall out of the window before a late refactor reaches them — the
confirmed cause of the N=5 continuity tax (run3 orphaned timeout-kwarg tests, run4 lost
Retry-After parsing). A bigger window only moves the cliff; it cannot give arbitrary-
distance continuity on a 100/1000-turn job.

WHAT: replace recency with RELEVANCE, organised around durable code *contracts*, not prose.
  1. ProjectState Ledger  — bounded, structured contracts (feature -> obligation + files/
     tests/symbols), SLM-updated each turn (compress-don't-drop).  Also the FeatureMap.
  2. Refactor Guard       — before a refactor/migrate turn (or one whose impacted files/
     symbols overlap an existing contract), surface the obligations to preserve/migrate.

Hardening (PR #44 /code-review, 2026-06-19): word-boundary matching (no substring over-fire),
empty/scalar payload sanitization (no crash, no ''-always-fires), bounded guard/state output,
top-level-array + parse-failure detection, save-failure surfacing, OpenAI->default judge
fallback (no provider lock-in).  All matching/serialization is pure-Python + one lazy,
injectable SLM call.  Persists a per-session JSON sidecar (same cwd-hash key as session.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

LEDGER_VERSION = 1
MAX_CONTRACTS = 40              # bound the ledger (keep most-recently-touched if exceeded)
STATE_SUMMARY_MAX_CHARS = 1800  # cap the always-on ProjectState header
GUARD_MAX_CHARS = 2200          # cap the refactor-guard checklist (PR#44 #4: was uncapped)

# Refactor/migration trigger words, matched on WORD BOUNDARIES (PR#44 #6: 'merge' must not
# fire on 'submerged'). The guard ALSO fires on impacted-file/symbol overlap + broad/new
# SLM scope, so keyword recall is a backstop, not the sole signal.
REFACTOR_KW = (
    "refactor", "migrate", "migration", "consolidate", "replace", "unify", "merge",
    "extract", "rename", "reorganize", "reorganise", "inline", "move to", "switch to",
    "fold into", "rework", "restructure",
)

# Files named in a raw prompt — broadened beyond *.py (PR#44 #12: config/doc contracts like
# setup.cfg / pyproject.toml / *.md were never surfaced). Restricted to known extensions so
# version strings like "1.5" are not mistaken for files.
_FILE_RE = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|pyi|pyx|toml|cfg|ini|md|rst|txt|json|ya?ml|sh|ps1|cmd)\b")


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _clean_list(items) -> list:
    """Order-preserving dedup that strips whitespace and drops empties (PR#44 #3). Reuses
    prpt.core.utils.unique_preserve_order when importable; falls back to a local impl so the
    module stays importable (and unit-testable) without prpt."""
    coerced = [str(x) for x in (items or [])]
    try:
        from prpt.core.utils import unique_preserve_order
        return unique_preserve_order(coerced)
    except Exception:
        seen, out = set(), []
        for x in coerced:
            s = x.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


def _mentions(token, low: str) -> bool:
    """Word-boundary membership of `token` in the lowercased prompt `low` (PR#44 #5: replaces
    raw `token in low` substring matching that over-fired on 'id'∈'invalid', 'a.py'∈'data.py',
    and '' ∈ anything). Underscores/dots in code symbols/filenames are handled by re.escape +
    non-alnum lookarounds (so 'connect_timeout' / 'a.py' match as whole tokens)."""
    t = (str(token) or "").strip().lower()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low) is not None


def _join_capped(lines, max_chars, omit_label="line(s)") -> str:
    """Join lines on '\\n' keeping WHOLE lines within max_chars (PR#44 #4/#11: no mid-word
    slice, no unbounded payload). The first line (a header) is always kept."""
    out, total = [], 0
    for i, ln in enumerate(lines):
        add = len(ln) + 1
        if out and total + add > max_chars:
            out.append("  ...[{0} more {1} omitted for length]".format(len(lines) - i, omit_label))
            break
        out.append(ln)
        total += add
    return "\n".join(out)


def _norm_feature(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


# ---------------------------------------------------------------------------
# Persistence (per-session sidecar, same cwd-hash key as prpt/session.py)
# ---------------------------------------------------------------------------
def _ledger_path(cwd: str) -> Path:
    key = hashlib.sha256(os.path.abspath(cwd).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "promptpilot_ledger_{0}.json".format(key)


def load_ledger(cwd: str) -> dict:
    p = _ledger_path(cwd)
    if not p.exists():
        return {"version": LEDGER_VERSION, "contracts": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("contracts"), dict):
            return {"version": LEDGER_VERSION, "contracts": {}}
        return d
    except Exception:
        return {"version": LEDGER_VERSION, "contracts": {}}


def save_ledger(cwd: str, ledger: dict) -> bool:
    """Persist the sidecar. Returns False on write failure (PR#44 #9: was silently swallowed,
    causing a stale-ledger continuity loss with ok=True reported)."""
    try:
        _ledger_path(cwd).write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def clear_ledger(cwd: str) -> None:
    try:
        _ledger_path(cwd).unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Ledger merge (compress-don't-drop upsert; payload-sanitizing)
# ---------------------------------------------------------------------------
def merge_contracts(ledger: dict, new_contracts: list, turn: int | None = None) -> dict:
    """Upsert extracted contracts. files/tests/symbols union-merge with strip+drop-empty
    (PR#44 #3) and tolerate scalar/None/non-iterable values without crashing (PR#44 #1)."""
    contracts = ledger.setdefault("contracts", {})
    for c in new_contracts or []:
        if not isinstance(c, dict):
            continue
        feat = _norm_feature(c.get("feature", ""))
        if not feat:
            continue
        cur = contracts.get(feat) or {"feature": feat, "files": [], "tests": [], "symbols": []}
        for k in ("files", "tests", "symbols"):
            incoming = c.get(k)
            if isinstance(incoming, str):
                incoming = [incoming]
            elif not isinstance(incoming, (list, tuple)):
                incoming = []          # PR#44 #1: scalar/dict/None -> ignore, never iterate it
            cur[k] = _clean_list([*cur.get(k, []), *incoming])
        if c.get("contract"):
            cur["contract"] = str(c["contract"]).strip()
        if turn is not None:
            cur["turn"] = turn
        contracts[feat] = cur
    if len(contracts) > MAX_CONTRACTS:
        kept = sorted(contracts.items(), key=lambda kv: kv[1].get("turn", 0), reverse=True)[:MAX_CONTRACTS]
        ledger["contracts"] = dict(kept)
    return ledger


# ---------------------------------------------------------------------------
# SLM contract extraction (the one lazy, injectable model call)
# ---------------------------------------------------------------------------
_EXTRACT_INSTR = (
    "You maintain a small, durable ProjectState ledger for a long coding session. "
    "From the latest turn, emit the DURABLE CONTRACTS it established or changed — public "
    "APIs, function/keyword arguments, features, and the tests that lock them — that LATER "
    "turns (especially refactors/migrations) must preserve or intentionally migrate. "
    "Emit ONLY obligations a future change could accidentally break. Be terse; reuse a "
    "stable kebab-case `feature` id across turns about the same feature."
)


def ledger_judge_available() -> bool:
    """True iff the ledger can extract contracts (PR#44 #14: don't lock to OpenAI). Prefers
    OPENAI_API_KEY (cheap gpt-5.4-nano) but accepts any default judge (Max/codex/anthropic)."""
    if os.environ.get("OPENAI_API_KEY"):
        return True
    try:
        from prpt.judges import get_default_judge
        return get_default_judge() is not None
    except Exception:
        return False


def _slm_extract_contracts(raw, memory_record, changed_files, target_files, judge=None) -> tuple:
    """Return (contracts, cost_usd, ok). ok=False means the call did NOT yield usable JSON
    (no judge / empty output / unparseable / wrong shape) — distinct from ok=True with an
    empty list ("nothing durable this turn"). `judge` is injectable for tests."""
    if judge is None:
        try:
            if os.environ.get("OPENAI_API_KEY"):
                from prpt.judges import OpenAiJudge       # cheap gpt-5.4-nano (intended default)
                judge = OpenAiJudge()
            else:
                from prpt.judges import get_default_judge  # PR#44 #14: fall back, no OpenAI lock-in
                judge = get_default_judge()
        except Exception:
            return [], 0.0, False
    prompt = (
        _EXTRACT_INSTR + "\n\n"
        "[Turn request]\n{raw}\n\n"
        "[Turn summary]\n{mr}\n\n"
        "[Files changed]\n{cf}\n"
        "[Predicted target files]\n{tf}\n\n"
        'Return ONLY JSON: {{"contracts":[{{"feature":"<kebab-id>",'
        '"contract":"<one-sentence obligation>","files":[...],"tests":[...],"symbols":[...]}}]}}. '
        "Use an empty list if nothing durable was established."
    ).format(
        raw=(raw or "")[:2000],
        mr=(memory_record or "(none)")[:600],
        cf=", ".join(changed_files) or "(none detected)",
        tf=", ".join(target_files) or "(none)",
    )
    try:
        from prpt.judges import extract_json
        text, cost, _wt = judge(prompt)
    except Exception:
        return [], 0.0, False
    cost = float(cost or 0.0)
    if not (text or "").strip():
        return [], cost, False          # empty output => the SLM call did not run
    data = extract_json(text)
    if data is None:
        return [], cost, False          # PR#44 #7: non-empty text but no parseable JSON => failure
    if isinstance(data, list):
        out = data                      # PR#44 #7: SLM returned a bare top-level contracts array
    elif isinstance(data, dict):
        out = data.get("contracts")
    else:
        out = None
    if not isinstance(out, list):
        return [], cost, False          # parseable JSON but wrong shape => failure (warn), not silent-empty
    return out, cost, True


def update_ledger(cwd, raw, spec, changed_files, turn=None, judge=None) -> tuple:
    """AFTER-turn hook: extract this turn's contracts and merge into the session ledger.
    Returns (ledger, cost_usd, ok). Warns LOUDLY when extraction OR persistence fails so a
    with_memory run can never silently degrade into a no-memory run."""
    memory_record = (getattr(spec, "memory_record", "") or "") if spec is not None else ""
    target_files = (getattr(spec, "target_files", []) or []) if spec is not None else []
    new, cost, ok = _slm_extract_contracts(raw, memory_record, changed_files or [], target_files, judge=judge)
    if not ok:
        print("  [ledger] WARNING: contract extraction produced no usable JSON (missing judge / "
              "SLM error / wrong-shape output) — this turn recorded NO contracts; with_memory is "
              "degrading toward a no-memory run.")
    ledger = load_ledger(cwd)
    merge_contracts(ledger, new, turn=turn)
    if not save_ledger(cwd, ledger):    # PR#44 #9: surface persist failures
        print("  [ledger] WARNING: failed to persist the ledger sidecar — the next turn will "
              "read a stale/empty ledger (silent continuity loss).")
        ok = False
    return ledger, cost, ok


# ---------------------------------------------------------------------------
# Refactor Guard + always-on state summary (the BEFORE-turn injection)
# ---------------------------------------------------------------------------
def _is_refactor(raw: str, spec) -> bool:
    low = (raw or "").lower()
    if any(_mentions(k, low) for k in REFACTOR_KW):   # PR#44 #6: word-boundary, not substring
        return True
    if spec is not None and getattr(spec, "scope", "") in ("broad", "new"):
        return True
    return False


def _impacted_files(raw: str, spec) -> set:
    files = set((getattr(spec, "target_files", []) or []) if spec is not None else [])
    files |= set(_FILE_RE.findall(raw or ""))
    return {f for f in files if f}


def guard_hits(ledger: dict, raw: str, spec) -> dict:
    """Contracts whose files OR symbols the current turn impacts (relevance, not recency).
    File/symbol membership uses exact set-intersection plus WORD-BOUNDARY prompt mentions
    (PR#44 #5: no substring/empty over-fire)."""
    low = (raw or "").lower()
    impacted = _impacted_files(raw, spec)
    impacted_base = {f.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower() for f in impacted}
    hits = {}
    for feat, c in ledger.get("contracts", {}).items():
        cfiles = set(c.get("files", []))
        cbase = {f.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower() for f in cfiles if f}
        file_hit = bool(cfiles & impacted) or bool(cbase & impacted_base) or any(_mentions(b, low) for b in cbase)
        sym_hit = any(_mentions(s, low) for s in c.get("symbols", []))
        if file_hit or sym_hit:
            hits[feat] = c
    return hits


def refactor_guard_checklist(cwd: str, raw: str, spec) -> str:
    """Build the preserve-or-migrate checklist of obligations the current turn endangers.
    Fires on file/symbol overlap, OR a refactor with no explicit overlap (surface all —
    bounded). Output is char-capped (PR#44 #4)."""
    ledger = load_ledger(cwd)
    contracts = ledger.get("contracts", {})
    if not contracts:
        return ""
    hits = guard_hits(ledger, raw, spec)
    refactor = _is_refactor(raw, spec)
    if refactor and not hits:
        hits = dict(contracts)
    if not hits:
        return ""
    lines = ["[MEMORY — prior contracts to PRESERVE (prefer back-compat); migrate only if required]"]
    for feat, c in hits.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
        if c.get("tests"):
            lines.append("    tests/call-sites that LOCK this — keep them GREEN: " + ", ".join(c["tests"]))
        if c.get("symbols"):
            lines.append("    symbols: " + ", ".join(str(s) for s in c["symbols"]))
    if refactor:
        # Stage-2-lite ADDITIVE-BIAS reword: the prior "migrate ... to the new design, or state why
        # removed" framing plausibly NUDGED destructive kwarg removal (the chain_long tax — see
        # memory_ab_result / SESSION_MEMORY_ROADMAP Stage 2). Bias explicitly toward back-compat;
        # validated against research/_oracle_groundtruth.py via research/stage2_guard_experiment.py.
        lines.append(
            "This turn is a refactor/migration. PREFER ADDITIVE / back-compat: keep the existing public "
            "names above WORKING and ADD the new form alongside them. Remove a public name ONLY if truly "
            "required, and if you do you MUST update every listed call-site and test in THIS change so none "
            "are orphaned. Do not silently drop any of the above.")
    return _join_capped(lines, GUARD_MAX_CHARS, omit_label="contract line(s)")


def ledger_state_summary(cwd: str, max_chars: int = STATE_SUMMARY_MAX_CHARS) -> str:
    """Always-on bounded overview of established contracts (the ProjectState header).
    Line-boundary truncation (PR#44 #11: no mid-word slice)."""
    contracts = load_ledger(cwd).get("contracts", {})
    if not contracts:
        return ""
    lines = ["[PROJECT STATE — established contracts so far]"]
    for feat, c in contracts.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
    return _join_capped(lines, max_chars, omit_label="contract(s)")


def memory_prefix(cwd: str, raw: str, spec) -> str:
    """The full per-turn memory injection: bounded ProjectState + refactor-guard checklist."""
    parts = [p for p in (ledger_state_summary(cwd), refactor_guard_checklist(cwd, raw, spec)) if p]
    return "\n\n".join(parts)
