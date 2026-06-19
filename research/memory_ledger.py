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
     tests/symbols), SLM-updated each turn (compress-don't-drop: a contract is never
     dropped merely for being old).  This is also the FeatureMap (feature -> artifacts).
  2. Refactor Guard       — before a refactor/migrate turn (or one whose impacted files/
     symbols overlap an existing contract), surface the obligations that must be preserved
     or intentionally migrated.  Aimed exactly at where continuity breaks (late refactors).

All bounded -> the token win survives.  Marginal cost ≈ one cheap gpt-5.4-nano call/turn.

This module is pure-Python + a single lazy SLM call (injectable for tests). It persists a
per-session JSON sidecar next to prpt's session JSONL (same cwd-hash key).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

LEDGER_VERSION = 1
MAX_CONTRACTS = 40          # bound the ledger (keep most-recently-touched if exceeded)
STATE_SUMMARY_MAX_CHARS = 1800

# Refactor/migration trigger words. NOTE (per review): keyword detection alone misses
# "change the clients to use one config" — so the guard ALSO fires on impacted-file/symbol
# overlap with an existing contract, and on a broad/new SLM scope. See _is_refactor / guard_hits.
REFACTOR_KW = (
    "refactor", "migrate", "migration", "consolidate", "replace", "unify", "merge",
    "extract", "rename", "reorganize", "reorganise", "inline", "move to", "switch to",
    "fold into", "rework", "restructure",
)

_PY_FILE_RE = re.compile(r"[A-Za-z0-9_./-]+\.py")


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


def save_ledger(cwd: str, ledger: dict) -> None:
    try:
        _ledger_path(cwd).write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    except Exception:
        pass


def clear_ledger(cwd: str) -> None:
    try:
        _ledger_path(cwd).unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Ledger merge (compress-don't-drop upsert)
# ---------------------------------------------------------------------------
def _norm_feature(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def merge_contracts(ledger: dict, new_contracts: list, turn: int | None = None) -> dict:
    """Upsert extracted contracts. Lists (files/tests/symbols) union-merge; the obligation
    text is replaced by the latest; `turn` records recency for the bound. A contract is
    NEVER dropped for being old — only the least-recently-touched are evicted past the cap."""
    contracts = ledger.setdefault("contracts", {})
    for c in new_contracts or []:
        if not isinstance(c, dict):
            continue
        feat = _norm_feature(c.get("feature", ""))
        if not feat:
            continue
        cur = contracts.get(feat) or {"feature": feat, "files": [], "tests": [], "symbols": []}
        for k in ("files", "tests", "symbols"):
            incoming = c.get(k) or []
            if isinstance(incoming, str):
                incoming = [incoming]
            cur[k] = list(dict.fromkeys([*cur.get(k, []), *[str(x) for x in incoming]]))
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


def _slm_extract_contracts(raw, memory_record, changed_files, target_files, judge=None) -> tuple:
    """Return (contracts, cost_usd, ok). ok=False means the SLM call did NOT run (e.g.
    missing OPENAI_API_KEY -> empty output) — distinct from ok=True with an empty list
    ("nothing durable this turn"). `judge` is injectable for tests."""
    if judge is None:
        try:
            from prpt.judges import OpenAiJudge
            judge = OpenAiJudge()
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
        return [], cost, False   # empty output => the SLM call did not run (no key / SDK)
    data = extract_json(text) or {}
    out = data.get("contracts") if isinstance(data, dict) else None
    return (out if isinstance(out, list) else []), cost, True


def update_ledger(cwd, raw, spec, changed_files, turn=None, judge=None) -> tuple:
    """AFTER-turn hook: extract this turn's contracts and merge into the session ledger.
    Returns (ledger, cost_usd, ok). Warns LOUDLY when ok=False so a with_memory run can
    never silently degrade into a no-memory run (the harness also guards on the key upfront)."""
    memory_record = (getattr(spec, "memory_record", "") or "") if spec is not None else ""
    target_files = (getattr(spec, "target_files", []) or []) if spec is not None else []
    new, cost, ok = _slm_extract_contracts(raw, memory_record, changed_files or [], target_files, judge=judge)
    if not ok:
        print("  [ledger] WARNING: contract extraction produced no output (missing "
              "OPENAI_API_KEY / SLM unavailable) — this turn recorded NO contracts; "
              "with_memory is degrading toward a no-memory run.")
    ledger = load_ledger(cwd)
    merge_contracts(ledger, new, turn=turn)
    save_ledger(cwd, ledger)
    return ledger, cost, ok


# ---------------------------------------------------------------------------
# Refactor Guard + always-on state summary (the BEFORE-turn injection)
# ---------------------------------------------------------------------------
def _is_refactor(raw: str, spec) -> bool:
    low = (raw or "").lower()
    if any(k in low for k in REFACTOR_KW):
        return True
    if spec is not None and getattr(spec, "scope", "") in ("broad", "new"):
        return True
    return False


def _impacted_files(raw: str, spec) -> set:
    files = set((getattr(spec, "target_files", []) or []) if spec is not None else [])
    files |= set(_PY_FILE_RE.findall(raw or ""))
    return files


def guard_hits(ledger: dict, raw: str, spec) -> dict:
    """Contracts whose files OR symbols the current turn impacts (relevance, not recency)."""
    low = (raw or "").lower()
    impacted = _impacted_files(raw, spec)
    impacted_base = {f.rsplit("/", 1)[-1].lower() for f in impacted}
    hits = {}
    for feat, c in ledger.get("contracts", {}).items():
        cfiles = set(c.get("files", []))
        cbase = {f.rsplit("/", 1)[-1].lower() for f in cfiles}
        file_hit = bool(cfiles & impacted) or bool(cbase & impacted_base) or any(b in low for b in cbase)
        sym_hit = any(str(s).lower() in low for s in c.get("symbols", []))
        if file_hit or sym_hit:
            hits[feat] = c
    return hits


def refactor_guard_checklist(cwd: str, raw: str, spec) -> str:
    """Build the preserve-or-migrate checklist of obligations the current turn endangers.
    Fires on: file/symbol overlap with a contract, OR a refactor with no explicit overlap
    (surface all — a broad refactor can touch anything; the ledger is bounded)."""
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
    lines = ["[MEMORY — prior contracts you MUST preserve or intentionally migrate]"]
    for feat, c in hits.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
        if c.get("tests"):
            lines.append("    tests/call-sites to keep or migrate: " + ", ".join(c["tests"]))
        if c.get("symbols"):
            lines.append("    symbols: " + ", ".join(str(s) for s in c["symbols"]))
    if refactor:
        lines.append(
            "This turn is a refactor/migration: do NOT silently drop the above — migrate their "
            "call-sites + tests to the new design, or explicitly state why each is removed.")
    return "\n".join(lines)


def ledger_state_summary(cwd: str, max_chars: int = STATE_SUMMARY_MAX_CHARS) -> str:
    """Always-on bounded overview of established contracts (the ProjectState header)."""
    contracts = load_ledger(cwd).get("contracts", {})
    if not contracts:
        return ""
    lines = ["[PROJECT STATE — established contracts so far]"]
    for feat, c in contracts.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
    return "\n".join(lines)[:max_chars]


def memory_prefix(cwd: str, raw: str, spec) -> str:
    """The full per-turn memory injection: bounded ProjectState + refactor-guard checklist."""
    parts = [p for p in (ledger_state_summary(cwd), refactor_guard_checklist(cwd, raw, spec)) if p]
    return "\n\n".join(parts)
