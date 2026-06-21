# Session-Memory — Consolidated Findings & Roadmap

**Status:** 2026-06-20. Single reference consolidating the A/B result, its postmortem, two third-party
reviews, the generalization matrix, and the SLM-reasoning redesign. Supersedes nothing; links the parts.

**Source artifacts (paths):**
- Design under test: [SESSION_MEMORY_ARCHITECTURE.md](SESSION_MEMORY_ARCHITECTURE.md)
- Investigation: [SESSION_MEMORY_AB_POSTMORTEM.md](SESSION_MEMORY_AB_POSTMORTEM.md)
- Third-party reviews (external codex worktree): `C:/Users/magicQ/.codex/worktrees/0257/LLM/docs/SESSION_MEMORY_AB_POSTMORTEM_REVIEW.md`, `…/SESSION_MEMORY_FIX_SCALING_RECOMMENDATION.md`
- Verdict in memory: `C:/Users/magicQ/.claude/projects/B--LLM/memory/memory_ab_result.md`
- Engine + fixture: `research/memory_ledger.py`, `research/chain_long_fixture.py`, `research/chain_test_v2.py`
- Run data: `research/data/chain_results_v2/codex/chain_long/` (+ archived native baseline `…/chain_long_PUBLISHED_9.69x_20260618/`)
- Workflow scripts (session dir `…/workflows/scripts/`): `with-session-continuity-forensics-max-*.js`, `with-memory-continuity-forensics-*.js`, `memory-generalization-matrix-*.js`, `slm-reasoning-memory-design-*.js`

---

## 1. Where we are (the A/B result)

chain_long, N=5, codex, slm-openai-v2, clean-main prpt, httpx baseline `d764bfc`. Arms: `with_session`
(recency window) vs `with_memory` (ProjectState ledger + refactor-guard).

- **Tokens — confirmed win:** `with_memory` ≈ **1.57× lighter than with_session, 12.2× than native** (total input, cache-independent). Stable across all 5 runs.
- **Continuity — thesis NOT supported:** matched-rigor (`effort:max`, baseline-isolated) forensics → **with_memory 4/5 taxed vs with_session 2/5.** On the *targeted* destructive-migration mode it's ~2 vs 1 (within N=5 noise) once degraded/bail runs are separated — so **"no improvement," not a clean "worse."**
- **Data quality:** zero-token timeout bug did **not** recur; 1 timeout reparse-recovered (0 final censored); 1 ledger-extraction failure (run2 T3 = read_timeout, but read_timeout survived).

---

## 2. Root cause (what the postmortem found)

1. **Proven — "recall ≠ action":** the ledger remembered the contract and the guard surfaced it (`ledger_ok=True`), yet the agent migrated the code and **still orphaned the tests/call-sites.** Surfacing ≠ preserving.
2. **Hypothesis (unproven, N=5):** the guard's *"migrate to the new design"* wording may **nudge destructive removal** over additive/back-compat — clean runs (both arms) kept old kwargs; taxed runs removed them.
3. **Ruled out:** v2 clarify-route defect (failed turns were `intent=act`); zero-token bug; the run-2 ledger miss (read_timeout survived).

**Deeper structural over-fit (from §5/§6):** the ledger schema is **code-centric** (`feature → {files, tests, symbols}`) and *all* the judgment is **mechanical** (regex/keyword/set-intersection). It can only see code-shaped, surface-overlapping, keyword-flagged failures — which is *why* it doesn't generalize.

---

## 3. Third-party reviews (verified, accepted)

**Postmortem review** (`…_AB_POSTMORTEM_REVIEW.md`) — all load-bearing claims verified against code:
- **P1 (accepted):** the 4/5 headline conflates failure modes → split into `destructive_migration` / `execution_miss` / `agent_bail` / `ledger_degraded`; treat `ledger_ok=False` runs as invalid. Once separated, it's ~parity on the targeted mode.
- **P2a (accepted, corrects me):** "surface concrete test files" is **already implemented** (`memory_ledger.py:322-323`); the real gap is action language + capturing `call_sites` separately from `tests`.
- **P2b (accepted):** unconditional "MUST keep back-compat" conflicts with T13's explicit "instead of"; adopt **policy-based** wording (additive default; if removal is explicitly requested, update every call-site/test).
- **P3b (accepted, corrects me):** `print_memory_ab` (line 1243) says "rewrite held constant" while `prepare_with_memory` (line 741) says it's *not* a pure swap → fix the contradiction.

**Scaling recommendation** (`…_FIX_SCALING_RECOMMENDATION.md`) — strong, complementary:
- **Adopt:** generic *contract-migration discipline* (not "keep kwargs"); a **typed schema** (`public_api/behavior/data_contract/config/integration/verification`) + a **`call_sites`/`consumers`** field; a **post-turn verifier**; the 6-way failure classification; a cross-language migration matrix.
- **Blind spot:** it generalizes contract *type* + *language* but keeps the *refactor-orphaning failure mode* — every fixture is "a late refactor endangers a contract" (chain_long re-skinned). It omits **invariants** (no surface/consumers) and **findings** (debugging) — the two highest-divergence modes the matrix found.

**Roadmap review** (`…/SESSION_MEMORY_ROADMAP_REVIEW.md`) — all code claims verified accurate:
- **P1a (accepted, reframes the verifier):** post-turn detection is **measurement, not a fix** — there is no N+1 turn after the final refactor, so a verdict alone repairs nothing. The real continuity lever is a **(light, gated) repair turn** or the cheap guard reword. → the verifier is reframed *instrument-first, fix-enabler-second*.
- **P1b (accepted, gated):** a fused verifier needs a **prior-ledger snapshot (taken before merge)** + a **bounded diff** as evidence — the current `_slm_extract_contracts(raw, memory_record, changed_files, target_files)` gets neither (verified). Reuse the `git diff` `capture_end_state` already takes. Built **only if Arm C is insufficient**.
- **P1c (accepted):** extend `merge_contracts` to carry `kind`/`anchorless`/`watch_for` (verified: it keeps only `feature/files/tests/symbols/contract/turn`, so new fields are silently dropped).
- **P2a (accepted):** per-fixture **oracle manifest**; expand chain_long beyond `pytest -k timeout` (verified — it missed the pool_size orphans); treat `pytest_no_match` (rc=5) as **invalid, not clean**.
- **P2b (accepted):** clarify experiment arms `A0`/`A1`/`C`/`B` (see §6).
- **P2c (accepted, softens §5):** keep "cost isn't the blocker" but enforce **budgets** (max contracts/diff-chars/evidence/output, fail-closed JSON) and **report** verifier input-tokens/latency/parse-fail/degraded-turn.
- **Calibration (user directive — keep the flow light):** the heavy machinery (evidence plumbing + verifier + repair) is **gated behind Arm C** — don't build it unless the cheap guard reword falls short.
- **One push-back (kept):** even verifier-as-instrument has standalone value (the non-circular detector that makes the whole experiment trustworthy + can retro-score existing data) — so "instrument first," not "useless without repair."

---

## 4. Generalization matrix (test failure modes chain_long can't)

chain_long only ever produces *one* failure shape (a late refactor removing a public kwarg). Six divergent
fixtures were designed to break the ledger's built-in assumptions; ranked by divergence × depth of gap exposed:

| # | fixture | failure mode chain_long can't surface | effort |
|---|---|---|---|
| **1 (build first)** | **Data/schema semantic drift** (toy Alembic event store) | mixed-units **with green tests** — contract lives in *data*, no symbol; guard wording *misdirects* (code-migrate vs data-migrate) | medium |
| **2** | **Global/architectural invariant** (greenfield + import-linter) | rule violated in *fresh code*, no refactor turn, no owning file; runnable `lint-imports` oracle | medium |
| **3 (strategic)** | **Long debugging chain** (pandas/aiohttp pinned bug) | state = ruled-out causes; tax = re-investigating a dead lead; **invisible to every current instrument** | high |
| 4 | cross-service wire contract | "untouched casualty" — break is on the side the agent left alone | high (redundant w/ #1) |
| 5 | SCALE (Django, 20+ call-sites) | open-ended/growing dependent set; tests `MAX_CONTRACTS`/`GUARD_MAX_CHARS` truncation | medium (deferred) |

**Coverage gaps no fixture hit:** confidently-*stale* contract (meaning drifts, files identical); contract
conflict/supersession; **over-fire *cost*** (irrelevant contracts injected when memory isn't needed — the cost
side of why with_memory could be worse); cross-run sidecar leakage; `_norm_feature` id-collision.

**Strategic takeaway:** the deeper fix is **extending the ledger schema to non-code contracts** (invariants,
decisions, findings) + a **per-turn trajectory judge** — not tuning the guard string. 4 of 6 fixtures break
schema-assumption A; the debugging one breaks every *instrument* (even `judge_continuity` reads only the end state).

---

## 5. SLM-reasoning redesign (move judgment out of the heuristics)

The system is mechanical exactly where it over-fits (relevance = regex/keyword/overlap). Five mechanisms were
designed to move judgment into SLM reasoning, with mechanical kept as a **floor + validator**.

**Cost is not the primary blocker (but treat it as a budgeted invariant, not "free"):** the token win lives on
the *subscription* side (agent transcript, 0.04–2.36M tok/turn); every proposed SLM call is *API-side nano*
(1.5–18k tok = **0.03–0.4%** overhead, under the cache-warmth noise floor). So **judge on reliability + whether
it attacks recall≠action, not on token price.** BUT the verifier needs diff-evidence + prior contracts that can
grow, and the real risks include latency, quota, truncation, parse-failure, and false confidence — so **enforce
budgets** (max contracts / diff-chars / evidence snippets / audit output; fail-closed JSON) and **report**
`verifier_input_tokens`, latency, parse-fail rate, and degraded-turn count in every run (roadmap-review P2c).

| rank | mechanism | verdict |
|---|---|---|
| **1** | **ObligationVerifier** — post-turn "did this break a promise?" judge. **Instrument first** (a non-circular detector; the pytest-reproducible target taxes make it labelable), **fix-enabler second.** | Needs a **prior-ledger snapshot (before merge) + a bounded diff** as evidence (roadmap-review P1b) — so it's one call but a *bigger input*, not literally free. **Detection alone is measurement, not a fix** (no N+1 after the final refactor); it only improves *continuity* when paired with the **light gated repair** below. Risk: *leniency* → mandatory exact-diff-line evidence (mechanical substring check) + asymmetric "drop-without-positive-migration = VIOLATED by default." |
| 2 | **Pre-turn Preservation Plan** (additive-biased, gated) | Encodes the prescribed fix proactively, but structurally closest to the v2-clarify defect and could nudge destructive removal *harder*. Build only if #1's correction lands too late. |
| 3 | **ObligationMiner** (open/typed *anchorless* extraction) | **Enabler** for #1/#2 to see the divergent fixtures (only one that can *represent* invariants/data-semantics/findings). Ship a **minimal slice** (`kind` + `anchorless` + `watch_for`), not the full open schema (invention-prone). |
| 4–5 | Risk-Reasoner / Relevance-compressor | Improve *which contract is surfaced* — necessary-but-insufficient (A/B proved it); #5 adds a new false-drop loss mode. Dominated by #1. |

**Circularity (the real constraint):** every detector is an SLM judging an SLM-rewritten + SLM-extracted turn.
Non-circular oracles: **pytest** (orphan/API class — apply diff to clean `d764bfc`, pass-clean/fail-patched);
**hand-labeled fixtures** (invariant/data-drift — no cheap mechanical oracle); grade nano with a **different
tier** (mini/Haiku); pre-register thresholds. `judge_continuity.py` cannot be the validator (shares circularity).

**What stays mechanical (floor + validator):** JSON/enum/anti-`?` validation; grounding checks (reject
hallucinated files; reject any VIOLATED whose evidence isn't a diff substring); the **union floor** (Pareto-safe);
degradation fallback; pytest as eval truth. SLM provides only the *semantic ceiling*.

---

## 6. Build order — measurement first, heavy machinery only if the data demands it

The guiding rule (user directive): **keep the flow light.** Stage 0 makes the measurement trustworthy; a decision
gate then decides whether the SLM machinery is built at all. **Item 1 (the policy guard reword) was DROPPED** — it
is a Level-2, migration-family-only fix with little long-term value; the real lever is the schema/reasoning path
(Stage 2), not a guard string.

### Stage 0 — measurement-validity + hygiene  (✅ IMPLEMENTED — this PR)
- ~~**Policy guard wording**~~ — **DROPPED** (migration-family band-aid; see above).
- ✅ `print_memory_ab` — drop "rewrite held constant" → "architecture-vs-architecture" (review P3b).
- ✅ `classify_run()` — report `ledger_degraded` / `no_edit_bail` runs **separately** from real regressions, so a degraded/bailed run isn't averaged in as clean (review P1).
- ✅ **Fixture-text fix** (`chain_long_fixture.py`) — inspection/search allowed; only test *execution* forbidden (caused run-4's bail). Both arms.
- ✅ **Oracle fix** (review P2a) — broaden `-k timeout` → `_ORACLE_K` (catches non-timeout orphans, e.g. pool_size); treat rc 5/124/125 as `pytest_valid=False` (**invalid, not clean**); `k_expr` = per-fixture oracle-manifest hook.
- ✅ `research/_test_stage0_fixes.py` — unit tests for `_pytest_flags`, the broadened oracle, and `classify_run`.

### Stage 1 — re-measure HONESTLY, then DECIDE
With item 1 dropped there is no cheap *fix* arm; Stage 1 is a corrected *measurement* of the existing tax:
- **Re-score (free, no paid run):** apply `classify_run()` + the broadened oracle to the existing A/B data → the honest, mode-separated taxed count (run2 was `ledger_degraded`, run4 a `no_edit_bail` — both drop out of the destructive-migration tally, likely pulling with_memory toward parity).
- **A1 (optional fresh run):** mechanical ledger + Stage-0 fixes, N=5, to confirm the honest tax on a clean run.

**Decision gate:** if the honest tax is already ~parity with native (the degraded/bail runs were inflating "4/5"),
the continuity gap is largely a *measurement* artifact and **no further machinery is built.** Only if a **real
residual destructive-migration tax remains** proceed to Stage 2.

### Stage 2 — ONLY IF a real residual tax remains (the SLM-reasoning path, gated)
1. **Schema-carry** (review P1c) — extend `merge_contracts` + persistence to keep `kind`/`anchorless`/`watch_for` (+ `call_sites`/`consumers`); unit-test round-trip + junk-sanitization.
2. **Evidence plumbing** (review P1b) — `_slm_extract_and_audit()`; snapshot the **prior ledger before merge**; capture a **bounded `git diff`** (reuse `capture_end_state`'s) + removed/added-symbol summary; return `{contracts:[…], audits:[{feature,status,evidence,suggested_repair}]}`.
3. **Verifier** (instrument-first) with anti-leniency + mandatory diff-substring evidence + the budgets/reporting from §5.
4. **Light gated repair** (review P1a, lightened — *not* an always-on loop): on a **high-confidence `violated`** verdict at a modifiable turn — and **immediately on the final turn** (no N+1 there) — fire **one** targeted repair turn, then re-capture end-state. Measure `violations_detected` vs `violations_repaired` separately.
5. **B** — `A1` + schema-slice + verifier + gated repair.

### The deciding comparison & metrics
- **Arms:** `A0` (archived) · `A1` (mech + Stage-0 fixes) · `B` (A1 + verifier + gated repair). **B vs A1** decides whether the SLM machinery reduces the residual tax. *(No Arm C — the guard-reword fix was dropped.)*
- **Primary metric = taxed-run count via the oracle** (`_ORACLE_K` / pytest on clean-baseline-applied diffs — non-circular), **not** tokens, **not** end-state (ceilings at 1.000), with `classify_run` excluding degraded/bail runs.
- **Secondary:** total tokens within warmth-noise of `A1`; verifier precision/recall vs the pytest oracle; `verifier_input_tokens`/latency/parse-fail/degraded-turn.
- **Static divergent-fixture eval** (off the chain): ~10 hand-labeled cases per class (ms-drift / cents-invariant / ruled-out-lead), graded by a **different tier** (mini/Haiku) against a fixed key.
- **Pre-register** the threshold ("B must reach ≤2/5 to beat A1") before running (n3-noise lesson).

### Generalization (after the chain_long experiment)
Build matrix fixture **#1 (data-semantic drift)** — medium effort, deterministic oracle — the first real test of
whether the tax/fix generalize beyond code contracts (and the one that shows a migration-style guard *misdirects*).

---

## 7. One-line summary

`with_memory` is a confirmed **token win** and a **continuity non-improvement**. First **re-measure honestly**
(the Stage-0 fixes — corrected oracle + degraded/bail classification — shipped in this PR) to see how much of the
"4/5" tax is real vs measurement artifact; **only if a real residual tax remains** build the heavier path — a
post-turn **SLM verifier (instrument-first) + a light gated repair**, fed real **diff + prior-ledger evidence**,
on a **schema that carries non-code contracts** — validated by **non-circular oracles** (pytest + hand-labeled
fixtures, graded by a different tier) and proven on **structurally different repos/tasks**, not just chain_long.
The migration-family guard reword was dropped. Cost isn't the blocker, but treat it as a **budgeted invariant**.
