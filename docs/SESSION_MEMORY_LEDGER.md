# Session Memory — the Contract Ledger + Refactor Guard (product feature)

**Status:** shipped in PR #50 (`prpt/memory.py` + `prpt/cli.py` wiring), **opt-in, off by default**.
This documents the *design as committed*. Lineage/evidence: `SESSION_MEMORY_ARCHITECTURE.md` (research
design), `SESSION_MEMORY_AB_POSTMORTEM.md` + `SESSION_MEMORY_ROADMAP.md` (the A/B that validated it),
`SESSION_MEMORY_VERIFY_REPAIR.md` (the deferred next tier this lays groundwork for).

---

## 1. What it is

PromptPilot wraps a coding-agent CLI across many turns. It needs *memory* so a later turn knows what
earlier turns built. There are now **two memory strategies**, chosen per invocation:

| Strategy | Flag | What it carries | Default |
|---|---|---|---|
| **Recency window** | `--memory recency` | the last `MAX_TURNS=4` user/assistant pairs (300-char-truncated), prepended to the **SLM rewrite** (`prpt/session.py`) | ✅ default |
| **Contract ledger** | `--memory ledger` (or `PROMPTPILOT_MEMORY=ledger`) | a structured, durable map of **public contracts** + a before-turn **refactor guard**, injected into the **downstream agent prompt** (`prpt/memory.py`) | opt-in |

`ledger` mode **replaces** the recency window (it does not run alongside it).

## 2. Why (the problem the ledger solves)

The recency window is a *recency* heuristic: on a chain longer than ~5 turns, an early-turn contract
(e.g. "Client accepts a per-request `connect_timeout`") falls out of the last-4-pairs window before a
late refactor reaches it. When the refactor then consolidates the API, it silently drops that
contract — the **destructive-migration tax** (orphaned call-sites/tests). A bigger window only moves
the cliff. The ledger replaces *recency* with **relevance**: it keeps contracts as durable structured
records and surfaces the *relevant* ones at a refactor turn regardless of how many turns ago they were
established (**distance-independent continuity**).

## 3. Data model

The ledger is a JSON sidecar, one per working directory, keyed by a cwd hash — the **same storage
pattern as `prpt/session.py`** (`tempfile.gettempdir()/promptpilot_ledger_<sha12>.json`):

```jsonc
{ "version": 1,
  "contracts": {
    "<feature-id>": {                 // stable kebab-case id, reused across turns
      "feature": "connect-timeout",
      "contract": "Client accepts a per-request connect_timeout that falls back to the default",
      "files":   ["httpx/_client.py"],          // where it lives
      "symbols": ["connect_timeout"],            // the public names that carry it
      "tests":   ["tests/client/test_client.py"],// what locks it green
      "turn":    1,                              // recency for bounded eviction
      "probe":   "..."                           // OPTIONAL executable check — inert in this PR;
                                                 // groundwork for verify-repair (capped length)
    }
  }
}
```

Bounds (all in `prpt/memory.py`): `MAX_CONTRACTS=40` (evict least-recently-touched), `PROBE_MAX_CHARS=4000`
(drop over-cap probe payloads), `GUARD_MAX_CHARS=2200` / `STATE_SUMMARY_MAX_CHARS=1800` (cap injected text).

## 4. The before-turn injection (`memory_prefix`)

In `ledger` mode, before the agent runs, PromptPilot prepends `memory_prefix(cwd, raw, spec)` to the
**downstream** prompt. It has two parts:

1. **ProjectState summary** (`ledger_state_summary`) — always-on, bounded list of established contracts:
   ```
   [PROJECT STATE — established contracts so far]
   - connect-timeout: per-request connect_timeout override on Client, falls back to default
   ```
2. **Refactor guard** (`refactor_guard_checklist`) — fires when the turn is a refactor (keyword/scope)
   **or** its files/symbols overlap a contract. On a refactor it leads with the **additive-bias
   directive** (the validated wording), then lists the at-risk contracts + their locking tests:
   > *"This turn is a refactor/migration. PREFER ADDITIVE / back-compat: keep the existing public names
   > listed below WORKING and ADD the new form alongside them. Remove a public name ONLY if truly
   > required, and if you do you MUST update every listed call-site and test in THIS change so none are
   > orphaned. Do not silently drop any of them."*

   The directive is emitted **first** (before the per-contract list) so the char-cap truncates the list,
   never the load-bearing instruction.

**Key design decision — it goes to the AGENT, not the SLM rewrite.** The recency window feeds the SLM
*rewrite* (`prompt_for_slm`); the ledger guard feeds the **downstream coding agent** (after the rewrite,
into `final_prompt`). The contracts/guard are obligations the *agent* must honor, so they must reach the
agent — a deliberate asymmetry vs the recency window.

## 5. The after-turn extraction (`update_ledger`)

After the agent runs, in `ledger` mode PromptPilot calls `update_ledger(cwd, raw, spec, modified, turn)`:
one cheap, **injectable** SLM call (gpt-5.4-nano-class via `prpt.judges`) that reads the turn request +
the agent's **ground-truth modified files** (`adapter.last_modified_files`) and emits the durable
contracts this turn established, which are merged (compress-don't-drop) into the ledger. Failures warn
loudly (never a silent no-op) and never crash a run that already produced edits.

It runs **only on a successful run that produced real edits** — `exit_code == 0` (which already folds in
the verify-gate outcome, so a failed verification suppresses it too) **and** a non-empty modified-files
list. A failed or no-op turn records nothing, so the ledger never preserves an obligation for an API that
never landed.

## 6. CLI integration (`prpt/cli.py`)

| Point | Recency (default) | Ledger (`--memory ledger`) |
|---|---|---|
| recency load (`load_recent_turns`) | prepend to `prompt_for_slm` | **skipped** |
| downstream prompt (`final_prompt`) | unchanged | **prepend `memory_prefix`** (contracts + guard) |
| after the turn | `append_turn` only | `append_turn` **+ `update_ledger`** — gated on `exit_code == 0` **and** real modified files |
| session reset (`new-session`, `checkpoint --clear`, `bootstrap` w/o `--append`, `restart`) | `clear_session` | `clear_session` **+ `clear_ledger`** (via `_reset_ledger_if_cleared`, gated on the `cleared` flag) |

The session transcript (`append_turn`) is still written in `ledger` mode (it's just not used for the
prompt) so handoff/`load_all_turns` keep working.

## 7. Cost & evidence

- **Cost:** one cheap SLM extraction call per turn (the contracts) + the injected prefix tokens; the
  guard surfacing is what trades a little context for tax-resistance.
- **Evidence** (research A/B, `SESSION_MEMORY_AB_POSTMORTEM.md`): the additive-bias guard eliminates the
  **destructive-drop tax** (0/10 on its target class, vs the old "migrate" framing's 4/5) at comparable
  correctness and ~1.07× lighter total tokens (N=10). Framing: *comparable correctness at lower cost*,
  not "memory makes the agent more correct."

## 8. Scope — what is NOT in this feature (yet)

- **Verify-repair** — `prpt/memory.py` ships `snapshot_ledger` + the capped `probe` field as
  groundwork, but nothing executes probes or repairs here; that is the next opt-in tier
  (`SESSION_MEMORY_VERIFY_REPAIR.md`, built on `prpt/verify.py`).
- The **SLM-direct-answer follow-up** sub-path in `cli.py` (a rare interactive branch) is not
  ledger-wired (noted in-code).
- **Encrypted reasoning** is out of scope (and unavailable) — the ledger reasons over requests + the
  agent's actual file edits, not chain-of-thought.

## 9. Files
- `prpt/memory.py` — ledger + guard + `memory_prefix` + `update_ledger` + `snapshot_ledger`.
- `prpt/cli.py` — `--memory` flag (+ `PROMPTPILOT_MEMORY`) and the four wiring points above.
- `tests/test_memory.py` — 20 tests incl. an end-to-end CLI test (ledger injects the guard; recency does not).
