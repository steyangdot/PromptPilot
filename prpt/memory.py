"""ProjectState Ledger + FeatureMap + Refactor Guard — PromptPilot's structured session-memory
layer: the relevance-based alternative to the recency window in ``prpt/session.py``. Ported into the
product 2026-06-29 from research/memory_ledger.py (validated in PR #44–#49); wired into the turn flow
via ``prpt/cli.py`` behind an opt-in flag.  Design: docs/SESSION_MEMORY_ARCHITECTURE.md §5–§7.

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
import sys
import tempfile
import time
from pathlib import Path

from prpt.session import SESSION_TTL   # single source of truth for idle-expiry (pairs with the recency session)

LEDGER_VERSION = 1
MAX_CONTRACTS = 40              # bound the ledger (keep most-recently-touched if exceeded)
PROBE_MAX_CHARS = 4000          # cap a stored verification probe (PR#49: bound sidecar growth; a
                                # real probe is a few hundred chars — an over-cap payload is dropped)
STATE_SUMMARY_MAX_CHARS = 1800  # cap the always-on ProjectState header
GUARD_MAX_CHARS = 2200          # cap the refactor-guard checklist (PR#44 #4: was uncapped)
TOMBSTONE_MAX_CHARS = 400       # Stage A: retired-contract section gets its OWN cap so it can
                                # never displace active contracts (design §6-3 / panel A10)
WIP_MAX_CHARS = 400             # Stage A: bounded unfinished-work note (overwrite-only, §6-5)

# Stage A (design §6-3): removal-intent keywords. A contract may be retired ONLY when the USER's
# request deterministically expresses removal intent about that contract — never from agent
# narration or SLM output. Single words match on word boundaries; phrases as substrings.
DESTRUCTIVE_KW = ("remove", "delete", "drop", "deprecate", "retire", "strip", "prune")
DESTRUCTIVE_PHRASES = ("clean up", "get rid of", "do away with")

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
    non-alnum lookarounds (so 'connect_timeout' / 'a.py' match as whole tokens). The boundary
    classes ALSO exclude '_' and '-' so a short token does not fire INSIDE a larger identifier
    ('timeout' must not match inside 'read_timeout', 'retry-after' not inside 'retry-after-cap')."""
    t = (str(token) or "").strip().lower()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9_-])" + re.escape(t) + r"(?![a-z0-9_-])", low) is not None


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
        # Idle-expiry: honor the SAME TTL as the recency session (load_recent_turns skips entries
        # older than SESSION_TTL). Without this, `--memory ledger` could resurrect contracts from a
        # session the recency path would already treat as stale. A ledger written before this field
        # existed (no `updated_at`) is grandfathered as fresh.
        ts = d.get("updated_at")
        if ts is not None and time.time() - float(ts) > SESSION_TTL:
            return {"version": LEDGER_VERSION, "contracts": {}}
        return d
    except Exception:
        return {"version": LEDGER_VERSION, "contracts": {}}


def save_ledger(cwd: str, ledger: dict) -> bool:
    """Persist the sidecar ATOMICALLY (temp file + os.replace) so a crash/interrupt mid-write can't
    leave truncated JSON that load_ledger would silently reset to empty (total contract loss — a
    regression vs the append-only JSONL recency this replaces). Returns False on write failure
    (PR#44 #9: was silently swallowed). The updated_at idle-expiry stamp is written onto a COPY so
    the caller's dict is left untouched."""
    p = _ledger_path(cwd)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = {**ledger, "updated_at": time.time()}   # stamp on a copy (no in-place mutation of caller)
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)                            # atomic on the same volume
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)               # don't leave a partial temp behind
        except Exception:
            pass
        return False


def clear_ledger(cwd: str) -> None:
    try:
        _ledger_path(cwd).unlink(missing_ok=True)
    except Exception:
        pass


def snapshot_ledger(cwd: str) -> dict:
    """A deep, detached copy of the current ledger (contracts as they stand) — taken BEFORE a
    turn's update so the Stage-2 verifier can check the PRIOR contracts against the post-turn
    tree (docs/SESSION_MEMORY_VERIFY_REPAIR.md §6.C evidence plumbing). json round-trip = no
    shared refs with the live ledger."""
    return json.loads(json.dumps(load_ledger(cwd)))


# ---------------------------------------------------------------------------
# Ledger merge (compress-don't-drop upsert; payload-sanitizing)
# ---------------------------------------------------------------------------
def _looks_like_test_target(t) -> bool:
    """True iff `t` is a plausible pytest target — a `.py` file path, optionally `path::node-id` —
    and NOT a prose description, a bare/planned test name, or a glob. Existence and node-id validity
    are checked downstream at collect time; this rejects only entries that are not paths at all
    (Tier-1 finding 2026-07-02: the SLM sometimes emitted 'a new unit test for X' or a bare function
    name into `tests`, which the guard then surfaced as an unverifiable target inflating UNRESOLVED)."""
    fp = str(t).strip().split("::", 1)[0]
    if not fp or fp.startswith("-") or " " in fp or not fp.endswith(".py"):
        return False
    return not any(ch in fp for ch in "*?()[]")


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
        # Coverage floor (Tier-1 finding): keep only entries in `tests` that LOOK like real pytest
        # targets (a `.py` path, optionally `::node-id`) — never prose, a bare/planned name, or a
        # glob. Real-looking-but-nonexistent paths are LEFT IN (their drift is measured as UNRESOLVED
        # at the gate); this rejects only non-paths, so it can never hide a deleted locking test.
        cur["tests"] = [t for t in cur.get("tests", []) if _looks_like_test_target(t)]
        if c.get("contract"):
            cur["contract"] = str(c["contract"]).strip()
        probe = c.get("probe")
        if probe:
            # Stage-2 executable verification probe (docs/SESSION_MEMORY_VERIFY_REPAIR.md §6.A),
            # generated + birth-validated separately; preserved across merges like `contract`.
            # PR#49: cap length — an over-cap payload is DROPPED (a truncated probe is malformed and
            # would fail birth-validation anyway), so the sidecar cannot bloat over long runs.
            probe = str(probe)
            if len(probe) <= PROBE_MAX_CHARS:
                cur["probe"] = probe
        if turn is not None:
            cur["turn"] = turn
        contracts[feat] = cur
    if len(contracts) > MAX_CONTRACTS:
        kept = sorted(contracts.items(), key=lambda kv: kv[1].get("turn", 0), reverse=True)[:MAX_CONTRACTS]
        ledger["contracts"] = dict(kept)
    return ledger


# ---------------------------------------------------------------------------
# Stage A — contract lifecycle: user-intent-conditioned, quarantined tombstones
# (design docs/SESSION_MEMORY_V2_DESIGN.md §6-3). Statuses: absent == active;
# "deprecating" == quarantined (guard STILL fires); "tombstone" == retired
# (excluded from guard/state, rendered names-only in a separately-capped section
# so the agent does not resurrect the feature).
# ---------------------------------------------------------------------------
def _is_active(c: dict) -> bool:
    return c.get("status") not in ("tombstone",)


def detect_removal_intent(raw: str, contract: dict) -> bool:
    """True iff the USER's raw request deterministically expresses removal intent about THIS
    contract: a destructive keyword (word-boundary) or phrase, AND a contract anchor (feature id,
    a symbol, or a file basename) both present. Deliberately conservative — under-firing is the
    safe direction (guard noise), over-firing is destructive amnesia."""
    low = (raw or "").lower()
    if not low:
        return False
    kw = any(_mentions(k, low) for k in DESTRUCTIVE_KW) or any(p in low for p in DESTRUCTIVE_PHRASES)
    if not kw:
        return False
    anchors = list(contract.get("symbols", []) or [])
    anchors += [f.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for f in (contract.get("files", []) or [])]
    feat = contract.get("feature", "")
    if feat:
        anchors.append(feat)
        anchors.append(feat.replace("-", " "))   # "retry-after" also matches "retry after"
    return any((_mentions(a, low) if " " not in str(a) else str(a).lower() in low) for a in anchors if a)


def propose_deprecations(ledger: dict, raw: str, turn: int | None = None) -> list:
    """Quarantine (status='deprecating') every ACTIVE contract the user's request asks to remove.
    Takes effect (tombstone) only via finalize_deprecations after the NEXT gate-green turn; the
    guard keeps firing for quarantined contracts in the meantime. Returns the feature ids marked."""
    marked = []
    for feat, c in (ledger.get("contracts") or {}).items():
        if not _is_active(c) or c.get("status") == "deprecating":
            continue
        if detect_removal_intent(raw, c):
            c["status"] = "deprecating"
            c["deprecate_turn"] = turn if turn is not None else c.get("turn", 0)
            marked.append(feat)
    return marked


def _test_file_part(t) -> str:
    """Contract `tests` entries may be file paths or pytest node-ids — compare on the file."""
    return str(t).split("::", 1)[0]


def finalize_deprecations(ledger: dict, gate_green: bool, turn: int | None = None,
                          tests_deleted=None) -> list:
    """One-turn quarantine: a 'deprecating' contract proposed on an EARLIER turn becomes a
    tombstone only when the CURRENT turn's gate is green. If the retiring turn DELETED the
    contract's locking tests (rather than migrating them), scoped green is insufficient — the
    contract stays quarantined (design §6-3c). The veto reads BOTH this turn's `tests_deleted`
    AND the `locking_tests_deleted` stamp persisted at deletion time (PR#51 review: the
    deletion happens on the REMOVAL turn, the finalize on a LATER green turn — by which time
    git may no longer show it). Returns the feature ids tombstoned."""
    deleted_fp = {_test_file_part(t) for t in (tests_deleted or [])}
    done = []
    for feat, c in (ledger.get("contracts") or {}).items():
        if c.get("status") != "deprecating":
            continue
        if turn is not None and c.get("deprecate_turn", 0) >= turn:
            continue                      # proposed THIS turn -> quarantine holds until next
        if not gate_green:
            continue                      # quarantine extends until a green turn
        own_fp = {_test_file_part(t) for t in c.get("tests", [])}
        if (deleted_fp & own_fp) or c.get("locking_tests_deleted"):
            continue                      # locking tests deleted, not migrated -> hold
        c["status"] = "tombstone"
        c["tombstone_turn"] = turn if turn is not None else c.get("turn", 0)
        done.append(feat)
    return done


def _maintain_deletion_stamps(ledger: dict, cwd: str, tests_deleted=None) -> None:
    """Persist (and lift) the deleted-locking-tests evidence on quarantined contracts.
    ADD a stamp when a quarantined contract's locking test is among this turn's deletions;
    LIFT a stamped entry when that test file exists on disk again (restored or migrated back)
    — otherwise the veto would hold forever with no recovery path."""
    deleted_fp = {_test_file_part(t) for t in (tests_deleted or [])}
    for c in (ledger.get("contracts") or {}).values():
        if c.get("status") != "deprecating":
            continue
        stamps = set(c.get("locking_tests_deleted", []))
        stamps = {s for s in stamps
                  if not os.path.isfile(os.path.join(cwd, _test_file_part(s)))}   # lift restored
        stamps |= {t for t in c.get("tests", []) if _test_file_part(t) in deleted_fp}
        if stamps:
            c["locking_tests_deleted"] = sorted(stamps)
        else:
            c.pop("locking_tests_deleted", None)


def _tombstone_section(contracts: dict) -> str:
    """Names-only retired list under its OWN cap (never displaces active contracts)."""
    dead = [(feat, c.get("tombstone_turn", 0)) for feat, c in contracts.items()
            if c.get("status") == "tombstone"]
    if not dead:
        return ""
    dead.sort(key=lambda x: x[1], reverse=True)   # most-recent-first
    lines = ["[RETIRED — deliberately removed earlier; do NOT resurrect]: "
             + ", ".join(feat for feat, _t in dead)]
    return _join_capped(lines, TOMBSTONE_MAX_CHARS, omit_label="retired name(s)")


def guard_test_targets(cwd: str, raw: str, spec) -> dict:
    """Stage A (design §6-1): the locking tests of the contracts THIS turn endangers, for the
    verify-gate's SECOND, contract-targeted invocation. Returns
    {"targets": [test paths/node-ids], "unlocked": [feature ids with NO recorded tests],
     "hits": [feature ids surfaced]}. Collect-validity is the verify side's job."""
    ledger = load_ledger(cwd)
    live = {f: c for f, c in ledger.get("contracts", {}).items() if _is_active(c)}
    hits = guard_hits({"contracts": live}, raw, spec)
    if not hits and _is_refactor(raw, spec):
        hits = live
    targets, unlocked = [], []
    for feat, c in hits.items():
        tests = [t for t in (c.get("tests") or []) if t]
        if tests:
            targets.extend(tests)
        else:
            unlocked.append(feat)
    return {"targets": _clean_list(targets), "unlocked": unlocked, "hits": list(hits.keys())}


# ---------------------------------------------------------------------------
# SLM contract extraction (the one lazy, injectable model call)
# ---------------------------------------------------------------------------
_EXTRACT_INSTR = (
    "You maintain a small, durable ProjectState ledger for a long coding session. "
    "From the latest turn, emit the DURABLE CONTRACTS it established or changed — public "
    "APIs, function/keyword arguments, features, and the tests that lock them — that LATER "
    "turns (especially refactors/migrations) must preserve or intentionally migrate. "
    "Record what the turn actually DID (the realized changes in the changed files, in light "
    "of the gate verdict) — not merely what was asked (Stage A: DONE, not ASKED). "
    "Emit ONLY obligations a future change could accidentally break. Be terse; reuse a "
    "stable kebab-case `feature` id across turns about the same feature. "
    "The `tests` field MUST list ONLY test targets that ALREADY EXIST in the repo — a file path "
    "(e.g. `tests/test_timeouts.py`) or a pytest node-id (`path::test_name`) — NEVER a prose "
    "description, a planned/'new' test you did not create this turn, or a bare function name; use an "
    "empty list if this turn established no real locking test. "
    "Also emit `wip`: ONE short line describing genuinely unfinished work this turn left "
    "behind (empty string if none) — it is carried verbatim to the next turn as an unverified note."
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


def _slm_extract(raw, memory_record, changed_files, target_files, gate_verdict=None,
                 judge=None) -> tuple:
    """Return (contracts, wip, cost_usd, ok). ok=False means the call did NOT yield usable JSON
    (no judge / empty output / unparseable / wrong shape) — distinct from ok=True with an
    empty list ("nothing durable this turn"). `judge` is injectable for tests. Stage A: the
    prompt carries the GATE VERDICT (DONE-not-ASKED) and requests a bounded `wip` note."""
    if judge is None:
        try:
            if os.environ.get("OPENAI_API_KEY"):
                from prpt.judges import OpenAiJudge       # cheap gpt-5.4-nano (intended default)
                judge = OpenAiJudge()
            else:
                from prpt.judges import get_default_judge  # PR#44 #14: fall back, no OpenAI lock-in
                judge = get_default_judge()
        except Exception:
            return [], None, 0.0, False
    prompt = (
        _EXTRACT_INSTR + "\n\n"
        "[Turn request]\n{raw}\n\n"
        "[Turn summary]\n{mr}\n\n"
        "[Files changed]\n{cf}\n"
        "[Gate verdict]\n{gv}\n"
        "[Predicted target files]\n{tf}\n\n"
        'Return ONLY JSON: {{"contracts":[{{"feature":"<kebab-id>",'
        '"contract":"<one-sentence obligation>","files":[...],'
        '"tests":["<existing test path or path::node-id ONLY — never a description; [] if none>"],'
        '"symbols":[...]}}],'
        '"wip":"<one line of unfinished work, or empty>"}}. '
        "Use an empty contracts list if nothing durable was established."
    ).format(
        raw=(raw or "")[:2000],
        mr=(memory_record or "(none)")[:600],
        cf=", ".join(changed_files) or "(none detected)",
        gv=(gate_verdict or "(not run)"),
        tf=", ".join(target_files) or "(none)",
    )
    try:
        from prpt.judges import extract_json
        text, cost, _wt = judge(prompt)
    except Exception:
        return [], None, 0.0, False
    cost = float(cost or 0.0)
    if not (text or "").strip():
        return [], None, cost, False    # empty output => the SLM call did not run
    try:
        data = extract_json(text)
    except Exception:
        return [], None, cost, False    # extract_json raised (e.g. RecursionError) => fail-soft
    if data is None:
        return [], None, cost, False    # PR#44 #7: non-empty text but no parseable JSON => failure
    wip = None
    if isinstance(data, list):
        out = data                      # PR#44 #7: SLM returned a bare top-level contracts array
    elif isinstance(data, dict):
        out = data.get("contracts")
        w = data.get("wip")
        if isinstance(w, str):
            wip = w.strip()[:WIP_MAX_CHARS]
    else:
        out = None
    if not isinstance(out, list):
        return [], None, cost, False    # parseable JSON but wrong shape => failure, not silent-empty
    return out, wip, cost, True


def _slm_extract_contracts(raw, memory_record, changed_files, target_files, judge=None) -> tuple:
    """Back-compat wrapper (pre-Stage-A 3-tuple interface): (contracts, cost_usd, ok)."""
    out, _wip, cost, ok = _slm_extract(raw, memory_record, changed_files, target_files, judge=judge)
    return out, cost, ok


def update_ledger(cwd, raw, spec, changed_files, turn=None, judge=None,
                  gate_verdict=None, tests_deleted=None) -> tuple:
    """AFTER-turn hook: extract this turn's contracts and merge into the session ledger.
    Returns (ledger, cost_usd, ok). Warns LOUDLY when extraction OR persistence fails so a
    with_memory run can never silently degrade into a no-memory run.

    Stage A additions (design §6): `gate_verdict` ("green"/"red"/None) feeds DONE-not-ASKED
    extraction AND drives the tombstone quarantine — deprecations proposed on EARLIER turns
    finalize only when THIS turn's gate is green (and none of their locking tests were deleted,
    `tests_deleted`); removal intent in THIS turn's raw quarantines matching contracts; the
    SLM's bounded `wip` note is OVERWRITTEN (never merged) each turn."""
    memory_record = (getattr(spec, "memory_record", "") or "") if spec is not None else ""
    target_files = (getattr(spec, "target_files", []) or []) if spec is not None else []
    new, wip, cost, ok = _slm_extract(raw, memory_record, changed_files or [], target_files,
                                      gate_verdict=gate_verdict, judge=judge)
    if not ok:
        print("  [ledger] WARNING: contract extraction produced no usable JSON (missing judge / "
              "SLM error / wrong-shape output) — this turn recorded NO contracts; with_memory is "
              "degrading toward a no-memory run.", file=sys.stderr)   # stderr: never pollute stdout in automation
    ledger = load_ledger(cwd)
    # Quarantine lifecycle BEFORE merging new contracts: earlier proposals finalize on this
    # turn's green; this turn's removal intent quarantines (effect from the NEXT green turn);
    # deletion evidence is stamped so the veto survives to the later finalizing turn.
    finalize_deprecations(ledger, gate_green=(gate_verdict == "green"), turn=turn,
                          tests_deleted=tests_deleted)
    merge_contracts(ledger, new, turn=turn)
    propose_deprecations(ledger, raw, turn=turn)
    _maintain_deletion_stamps(ledger, cwd, tests_deleted=tests_deleted)
    # overwrite-not-merge: the WIP note cannot accrete (design §6-5); empty clears it. On a
    # FAILED extraction the old note is dropped too (PR#51 review P2): injecting last turn's
    # WIP as "the previous turn reported" would be false provenance — stale narrative is
    # worse than none.
    if ok and wip:
        ledger["wip"] = {"text": wip, "turn": turn}
    else:
        ledger.pop("wip", None)
    if not save_ledger(cwd, ledger):    # PR#44 #9: surface persist failures
        print("  [ledger] WARNING: failed to persist the ledger sidecar — the next turn will "
              "read a stale/empty ledger (silent continuity loss).", file=sys.stderr)
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
        if not _is_active(c):
            continue                    # Stage A: tombstoned contracts are no longer obligations
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
    contracts = {f: c for f, c in ledger.get("contracts", {}).items() if _is_active(c)}
    if not contracts:
        return ""
    hits = guard_hits(ledger, raw, spec)
    refactor = _is_refactor(raw, spec)
    if refactor and not hits:
        hits = dict(contracts)          # active-only: tombstones must not resurrect via the fallback
    if not hits:
        return ""
    lines = ["[MEMORY — prior contracts you MUST preserve; prefer back-compat, migrate only if required]"]
    if refactor:
        # Stage-2-lite ADDITIVE-BIAS reword (validated: research/stage2_guard_experiment.py). The prior
        # "migrate ... to the new design / state why removed" framing was a ~no-op that let destructive
        # kwarg removal through (the chain_long tax — memory_ab_result / ROADMAP Stage 2).
        # Emit this directive FIRST (right after the header), NOT appended last: _join_capped drops whole
        # TRAILING lines at GUARD_MAX_CHARS, so a last-appended directive silently vanished under many
        # surfaced contracts — exactly the multi-contract refactor regime it exists to fix (code-review #1).
        # Front-placement keeps the load-bearing instruction; the per-contract list truncates instead.
        lines.append(
            "This turn is a refactor/migration. PREFER ADDITIVE / back-compat: keep the existing public "
            "names listed below WORKING and ADD the new form alongside them. Remove a public name ONLY if "
            "truly required, and if you do you MUST update every listed call-site and test in THIS change "
            "so none are orphaned. Do not silently drop any of them.")
    for feat, c in hits.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
        if c.get("tests"):
            lines.append("    tests/call-sites that LOCK this — keep them GREEN: " + ", ".join(c["tests"]))
        else:
            # Stage A (§6-1b): tell the agent — honestly — that this obligation is unverifiable.
            lines.append("    (UNLOCKED — no locking tests recorded; this obligation cannot be "
                         "auto-verified, take extra care)")
        if c.get("symbols"):
            lines.append("    symbols: " + ", ".join(str(s) for s in c["symbols"]))
    return _join_capped(lines, GUARD_MAX_CHARS, omit_label="contract line(s)")


def ledger_state_summary(cwd: str, max_chars: int = STATE_SUMMARY_MAX_CHARS) -> str:
    """Always-on bounded overview of established contracts (the ProjectState header).
    Line-boundary truncation (PR#44 #11: no mid-word slice)."""
    contracts = {f: c for f, c in load_ledger(cwd).get("contracts", {}).items() if _is_active(c)}
    if not contracts:
        return ""
    lines = ["[PROJECT STATE — established contracts so far]"]
    for feat, c in contracts.items():
        lines.append("- {0}: {1}".format(feat, c.get("contract", "")).rstrip())
    return _join_capped(lines, max_chars, omit_label="contract(s)")


def _wip_note(ledger: dict) -> str:
    """Stage A (§6-5): the bounded unfinished-work note, rendered as UNVERIFIED narrative —
    the agent is told its provenance so hallucinated/stale WIP cannot masquerade as state."""
    wip = ledger.get("wip") or {}
    text = (wip.get("text") or "").strip()[:WIP_MAX_CHARS]
    if not text:
        return ""
    return "[WIP — the previous turn reported (unverified): {0}]".format(text)


def memory_prefix(cwd: str, raw: str, spec) -> str:
    """The full per-turn memory injection: bounded ProjectState + refactor-guard checklist
    + (Stage A) the separately-capped retired-contract list and the unverified WIP note."""
    ledger = load_ledger(cwd)
    parts = [p for p in (
        ledger_state_summary(cwd),
        refactor_guard_checklist(cwd, raw, spec),
        _tombstone_section(ledger.get("contracts", {})),
        _wip_note(ledger),
    ) if p]
    return "\n\n".join(parts)
