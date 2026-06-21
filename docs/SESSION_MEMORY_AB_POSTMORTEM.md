# Session-Memory A/B Postmortem — `with_memory` continuity regression

**Date:** 2026-06-20
**Experiment:** `chain_long` (13-turn dependent chain, compaction regime), codex, **N=5**, `--normalizer slm-openai-v2`, clean-main `prpt`, httpx baseline `d764bfc`.
**Arms:** `with_session` (recency-window bounded session) vs `with_memory` (ProjectState **ledger + refactor-guard**, relevance-not-recency).
**Related:** [SESSION_MEMORY_ARCHITECTURE.md](SESSION_MEMORY_ARCHITECTURE.md) (the design under test), [COMPACTION_REGIME_TEST.md](COMPACTION_REGIME_TEST.md) (the fixture + harness).

---

## TL;DR

The memory system delivered its **cost** goal and missed its **continuity** goal — the reason it was built.

- **Tokens (cache-independent total):** `with_memory` **9.9M/run** vs `with_session` **15.6M** = **1.57× lighter**; vs native `builtin` **120.4M** = **12.2× lighter**. Clear, stable win.
- **Continuity (matched-rigor forensics):** `with_memory` **4/5 runs taxed** vs `with_session` **2/5**. The ledger/refactor-guard did **not** prevent the orphaned-contract tax — it correlates with *more* of it.

**Verdict:** the central hypothesis — *"a relevance-based ProjectState ledger + refactor-guard reduces the orphaned-contract continuity tax"* — is **not supported by this run; the data points the other way.** The token efficiency is the real result; reposition the memory system as a **cost optimization**, not a continuity fix, pending the fix below.

---

## 1. What we were testing

`chain_long` incrementally builds a resilience/observability layer on httpx over 13 turns, then **refactors it** at the end. The durable contracts (and their source turns):

| ID | contract | turn |
|----|----------|------|
| C1 | `connect_timeout` per-request override, sync + async, fallback-to-default | T1–T2 |
| C2 | `read_timeout` override, both clients | T3 |
| C3 | Retry-After header **wired into** the retry path | T4 |
| C4 | Retry-After accepts HTTP-date **and** delta-seconds | T5 |
| C5 | retry delay capped at configurable max (default 60s) | T6 |
| C6 | per-request `elapsed` on `Response` | T7 |
| C7 | `elapsed` exposed via optional event hook | T8 |
| C8 | `httpx/_stats.py` collector wired into Client + exported | T9 |
| C9 | `pool_size` override wired through to transport | T10–T11 |
| C10 | `ResilienceConfig` consolidating connect/read-timeout/retry/pool_size | **T12** |
| C11 | both clients migrated to **use** `ResilienceConfig` *instead of* individual kwargs | **T13** |

The **tax** is an *orphaned contract*: a capability built early (C1–C9) that is dropped or broken by the late **T12/T13 refactor**. The memory system's refactor-guard is supposed to fire at T12/T13 and surface *"preserve/migrate these contracts + their tests."*

---

## 2. Symptom

`with_memory` is cheaper but **less continuous** than the recency-window baseline it was meant to beat.

| run | `with_session` (re-scored at `effort:max`) | `with_memory` (`effort:high`) |
|-----|--------------------------------------------|-------------------------------|
| 1 | **TAX** — Retry-After (C3) left unwired (dead function) | **TAX** — migration dropped connect/read-timeout kwargs → **17 orphaned-test TypeErrors** |
| 2 | clean — *additive* migration | **TAX** — `ResilienceConfig`/stats never landed (T9/T12/T13 `changed=[]`) |
| 3 | clean — *additive* migration | **TAX** — migration removed `limits=` kwarg → orphaned upstream `test_pool_timeout` |
| 4 | **TAX** — removed kwargs → **5 orphaned-test TypeErrors** | **TAX** — agent **bailed** on T13 (misread "no tests" as "no commands") |
| 5 | clean — clean consolidation, tests migrated in lockstep | clean |
| **taxed** | **2 / 5** | **4 / 5** |

Token cost over the same runs: `with_memory` 7.75 / 9.37 / 11.87 / 10.62 / 9.95M (mean **9.91M**); `with_session` mean **15.57M**.

---

## 3. Smoking gun

Three pieces of hard evidence, in order of strength.

### 3a. Isolated, reproduced regressions (the strongest)
For every "real regression" claim, a verifier created **two throwaway git worktrees off the clean baseline `d764bfc`**, applied the run's captured diff to one, and ran the *identical* `pytest -k timeout` on both.

- **`with_session` run 4** (the baseline's worst run): patched tree → `7 failed, 20 passed`; clean tree → `2 failed, 20 passed`. The **delta is exactly 5 TypeErrors** — `build_request()/get() got an unexpected keyword argument 'connect_timeout'/'read_timeout'`. Those 5 test IDs **don't exist** on the clean tree (`no tests ran`) — they were *added by the same diff* that removed the kwargs they call. The 2 shared failures (`test_write_timeout[asyncio]/[trio]`, "DID NOT RAISE", `write=1e-6`) fail identically on both → **pre-existing flake, not a regression.**
- **`with_memory` run 3**: same method — applying the diff to baseline turns `test_pool_timeout` from **2 passed → 2 TypeError** (`AsyncClient.__init__() got an unexpected keyword argument 'limits'`). The migration removed the public `limits=` kwarg with no back-compat shim, orphaning an **upstream** test the run never touched.

This isolation is what makes the tally trustworthy: a naive "pytest rc=1 in every run" reading would be wrong — **in 8 of 10 runs the only failure is the `test_write_timeout` flake.**

### 3b. The additive-vs-destructive split
Every **clean** run (both arms) performed an **additive** migration: it added `resilience=`/`resilience_config=` *alongside* the retained individual kwargs, so nothing was orphaned. `with_session` run 5 even migrated the tests in lockstep. Every **taxed-by-regression** run *removed* the kwargs and left the earlier-turn tests calling them.

> The contract was preserved as a *capability* in nearly every run (C1/C2/C5/C9 remained reachable via `ResilienceConfig`). What broke was the **test/call-site surface** — the migration changed the public API and forgot the call sites.

### 3c. The guard fired and the agent ignored it
In the taxed `with_memory` runs the contracts **were in the ledger** (`ledger_ok=True` on the relevant turns) — so the refactor-guard should have surfaced them at T12/T13 with the checklist *"tests/call-sites to keep or migrate."* The agent migrated the code and **still** orphaned the tests. Surfacing the contract did not change the behavior.

---

## 4. Root cause

**Primary (proven): surfacing a contract is necessary but not sufficient.** The orphaned-contract tax is fundamentally a *"changed the API, forgot the call sites/tests"* failure. The ledger correctly remembers *what* the contracts are, and the guard correctly surfaces them — but neither forces the agent to **update the dependent tests/call-sites** when it migrates. Both arms exhibit the tax whenever the agent chooses a *destructive* migration; memory's recall of the contract doesn't intervene at the moment that matters (writing the migration).

**Secondary (hypothesis — unproven, N=5):** the memory arm taxed *more often* (4/5 vs 2/5), and the most plausible mechanism is that the **guard's wording nudges destructive migrations.** The guard says *"this turn is a refactor/migration: do NOT silently drop the above — **migrate** their call-sites + tests to the new design, or explicitly state why each is removed."* Combined with T13's literal instruction (*"use `ResilienceConfig` **instead of** the individual kwargs"*), this "migrate to the new design" framing appears to push the agent toward **removing** the old kwargs (a "clean" migration) rather than the safer **additive/back-compat** path the clean runs took. So the guard intended to *preserve* contracts may, ironically, *increase* the rate of API-removing migrations that orphan tests.

**What it is *not* (ruled out):**
- **Not the v2 clarify-route defect.** Suspected for run 2, but its failed turns were all routed `intent=act` (not `clarify`) — genuine execution misses, not the [V2_CLARIFY_ROUTE_POSTMORTEM](V2_CLARIFY_ROUTE_POSTMORTEM.md) bug.
- **Not the zero-token timeout bug.** Across 130 turns only 1 timed out (`with_session` run3 T12, 6.99M **real** tokens), reparse-recovered → 0 final censored. The `audit_uncached_timeout_bug` failure mode did not recur.
- **Not the run-2 ledger miss.** The ledger failed to record `read_timeout` (C2) at T3 (`ledger_ok=False`), but `read_timeout` **survived** in run 2's final state (verified present on both clients) — the gap did not cause an orphaning.

---

## 5. Proposed fix

The lever the data hands us: **clean runs did additive, back-compat migrations and updated their tests.** Make the guard push the agent toward that behavior instead of toward destructive removal.

### Fix 1 — reword the refactor-guard (primary, cheap)
Change the guard checklist in `research/memory_ledger.py` (`refactor_guard_checklist`) from *"migrate/preserve the contracts"* to something that biases toward back-compat **and** explicitly demands the test/call-site update:

> **Before this refactor lands, for each contract below you MUST:**
> 1. Keep the existing public API working (prefer **adding** the new form *alongside* the old kwargs — back-compat — over removing them).
> 2. If you *do* remove or rename a public kwarg/parameter, **find and update every call-site and test** that uses it in the same change (don't leave them calling the old surface).
> 3. Update the contract's listed **tests** to the new design.

The current guard already lists `tests/call-sites to keep or migrate`, but as advice; the fix makes "keep back-compat OR update the call-sites+tests" a hard, enumerated step.

### Fix 2 — surface the concrete test files (stronger)
The ledger already stores each contract's `tests:[...]`. Have the guard name them explicitly per contract (*"update `tests/client/test_client.py`, `tests/client/test_async_client.py`"*) so the agent has the exact files to fix, not a general reminder.

### Fix 3 — re-test (the experiment that decides it)
Run a small **N=3–5** `with_memory` re-test with the reworded guard. Success criterion: the orphaned-kwarg regressions (run1/run3-style) drop, moving `with_memory` toward `with_session`-or-better continuity while keeping the ~1.6× token win. This is the only path that can rescue the continuity claim.

---

## 6. Caveats / open questions

- **N=5.** The 2-vs-4 split is directionally clear but not statistically airtight; the secondary root cause (guard framing) is a **hypothesis**, not established.
- **Rigor asymmetry (in the baseline's favor).** The `with_session` re-pass was `effort:max` with a fixed verify contract-ID bug and full diff-isolation; the `with_memory` pass was `effort:high` and had a verify bug (verifiers mapped `Cn`→Turn `n`, so some drop-verdicts checked the wrong contract). The baseline got the *harder* scrutiny — which only strengthens "memory is worse" — but a `with_memory` `effort:max` re-pass would make the comparison fully symmetric. The `with_memory` taxes are concrete (isolated regressions, `changed=[]` bails) so ≥4/5 is expected to hold.
- **End-state scoring limits.** The recorded `pytest -k timeout` selection misses non-timeout orphans (e.g. some `pool_size` tests); the forensics caught these by re-running, but the harness's headline rc under-reports them.

---

## Appendix — methodology & artifacts

- **Token metric:** total input tokens per run, summed across all turns (cache-independent; per [MEASUREMENT_METHODOLOGY](MEASUREMENT_METHODOLOGY.md)). Native `builtin` figures are cross-run from the archived published 9.69× run (legit on a total-token basis).
- **Forensic method:** one analyzer agent per run (11-contract end-state check from the captured `endstate_*_run{N}.json` diff + per-turn scores), then adversarial verifiers per tax flag; regression flags isolated by re-applying the run's diff to a clean `d764bfc` worktree and diffing the `pytest -k timeout` outcome vs an un-patched control.
- **Workflow scripts (session dir):** `with-session-continuity-forensics-max-wf_343838b6-3a9.js` (the clean, bug-fixed, `effort:max` template — reuse for the `with_memory` MAX re-pass), `with-memory-continuity-forensics-wf_c025ad25-68b.js`.
- **Data:** `research/data/chain_results_v2/codex/chain_long/` (this A/B); archived native baseline at `chain_long_PUBLISHED_9.69x_20260618/`.
- **Memory notes:** `memory_ab_result.md` (verdict), `memory_ab_run_in_progress.md` (run ops).
