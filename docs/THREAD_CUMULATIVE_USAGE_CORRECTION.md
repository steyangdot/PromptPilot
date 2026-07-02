# Thread-Cumulative Usage Correction (native-arm token accounting)

**Fix:** commit `5d864c8` — *"fix(research): thread-cumulative usage correction + verify-always gate
in the chain harness"* (2026-07-01).
**Shipped in:** **[PR #51 — Session-memory v2](https://github.com/steyangdot/PromptPilot/pull/51)**
(merged `9ef6ee9`, 2026-07-02).
**Code:** `research/chain_test_v2.py` (`delta_cumulative_usage`, `rebuild_native_delta_chain`).
**Tests:** `research/_test_cumulative_usage.py`.

---

## TL;DR

Codex `exec resume` reports **thread-cumulative** token counters — turn *N*'s `usage` covers turns
**1..N** of the whole thread, not just turn *N*. Our harness was **summing per-turn usage**, so it
**double-counted native-arm tokens by ≈ ×(N+1)/2**. Every previously-published codex native-arm
advantage was inflated by exactly this artifact; the correction deltas each turn against the previous
turn's raw cumulative reading to recover true marginal usage. Only **codex native-resume arms** were
affected; `claude-code -p --resume` reports per-invocation usage and was never wrong.

## The bug

- **When:** codex CLI builds since **~2026-06-14** switched `turn.completed` usage to thread-cumulative
  semantics. Runs before that reported per-invocation usage (so pre-~06-14 numbers are unaffected).
- **Which arms:** only arms that resume a native codex thread — `NATIVE_RESUME_ARMS = ("builtin",
  "slm_native", "stacked")`. `claude-code` per-invocation usage is unaffected.
- **The double-count:** summing `usage.input_tokens` across a resumed thread's turns adds
  `t1 + (t1+t2) + (t1+t2+t3) + …`, i.e. inflation ≈ **×(N+1)/2** for an N-turn chain.

## Why it mattered — the refuted headlines

Re-running the audit with naive summing **reproduced every published native-arm figure**, proving those
numbers *were* the artifact. After correction (total = bounded/native gross; uncached = uncached-only):

| Benchmark | Published (naive-sum) | Corrected |
|---|---|---|
| compaction regime | **9.69×** | **~1.71×** |
| v2-total (README/BENCHMARKS/wiki, PR #42) | **4.19× total / 1.97× uncached** | **1.34× total / 0.48× uncached — INVERTED** |
| tool-flip | **1.87× / 1.86×** | **1.28× / 1.20× total; 0.47× / 0.50× uncached — INVERTED** |
| chain1-era | 2.36× / 2.56× | *unchanged* (predates the ~06-14 switch) |

**Corrected doctrine:** the **total-token** direction survives but shrank to ~**1.2–2.4×**
(regime-dependent); **uncached was never a win** — several of the old uncached "advantages" *inverted*
once the double-count was removed. "Lead with total-token" as a framing survives; the magnitudes and
all uncached claims do not.

## The fix

Two functions in `research/chain_test_v2.py` convert cumulative readings into true marginal per-turn usage.

### `delta_cumulative_usage(usage, prev_raw) -> (per_turn_usage, semantics)`
- **Token fields** (`input/cached/uncached/output` = `_CUM_TOKEN_FIELDS`) are **delta'd** against the
  previous turn's *raw cumulative* reading.
- **Per-invocation fields** (`tool_calls`, agent messages, events) come from the single stream and
  **pass through unchanged**.
- **First turn** (no `prev_raw`): cumulative == marginal → pass through, `semantics="thread_cumulative_first"`.
- **Normal case:** `semantics="thread_cumulative_delta"`.
- **Version-robust fallback:** if **any** token delta would go **negative**, the readings are *not*
  cumulative (old pre-~06-10 codex, or the thread was never actually resumed) → return the **raw**
  value, `semantics="per_invocation_non_monotone"`. Never fabricate a delta.

### `rebuild_native_delta_chain(records) -> n_changed`
Recomputes a whole run's per-turn deltas from the stored `usage_cumulative_raw`, in turn order (used by
the reparse pass). Four properties make it correct:

1. **Mode is chain-wide / sticky** (PR #51 review): a thread is **either** cumulative **or**
   per-invocation, never mixed. If *any* adjacent raw pair is non-monotone **in any token field**
   (`input/cached/uncached/output` — a truly cumulative thread is monotone in *every* counter), the
   **whole** chain keeps raw values. The canonical case `500 → 300 → 450` must record `500, 300, 450`
   — *not* delta turn 3 against turn 2 into `150`.
   *Hardened in PR #52 (review P1):* the precheck originally keyed on `input_tokens` monotonicity
   alone, which **mixed deltas and raw within one chain** when input rose while cached/uncached/output
   dropped (`delta_cumulative_usage` then fell back per-*turn*); now all `_CUM_TOKEN_FIELDS` are
   checked, regression-tested with exactly that mixed-field case.
   (The live loop mirrors this with a sticky `cum_mode_ok` latch — but the latch is *prefix-greedy*:
   turns recorded before a mid-chain flip stay delta'd in the live record. The rebuild is the
   retroactive authority — the reparse pass applies it whenever it changes a chain — and every record
   preserves `usage_cumulative_raw`, so the rebuild can always be re-applied from ground truth.)
2. **Censored-gap absorption:** a killed/timed-out turn (input==0, nothing flushed) does **not** advance
   the baseline, so the *next* turn's delta spans across it → **run totals stay correct**; the
   attribution to the censored turn is documented, not silently dropped.
3. **Reparse redistribution:** if a censored turn's flushed cumulative reading is recovered *later*,
   both that turn's delta **and** the following turn's (which had absorbed the gap) change → the chain
   is rebuilt end-to-end.
4. **Raw preserved:** every record keeps `usage_cumulative_raw` (the untouched cumulative reading) so a
   re-parse can always redo the math from ground truth.

Semantics labels emitted: `thread_cumulative_first`, `thread_cumulative_delta`,
`per_invocation_non_monotone`, `per_invocation_sticky`.

## Tests

`research/_test_cumulative_usage.py` covers: first-turn pass-through; cumulative delta; non-monotone
fallback; plain-chain rebuild; **censored-gap-then-recovery** redistribution; old-artifact passthrough;
**chain-wide mode** (the `500→300→450` reviewer case, a late non-monotone pair flipping the whole
chain, and the PR #52 **mixed-field** case — input monotone while cached drops → whole chain raw); and
a **fixture-grounded** check that rebuilding a real 13-turn cumulative series yields
`sum(per-turn deltas) == last-turn cumulative` (gross **and** uncached).

**Enforcement honesty (PR #52 review P2):** the fixture-grounded check runs against a **tracked**
distillation (`research/fixtures/cumulative_usage_builtin_run1_distilled.json` — turn + usage fields
only, from the real `chain_long/builtin_run1.json`), so it holds on a clean checkout. The check
against the **full raw artifact** is opportunistic/local-only: `research/data/` is gitignored, and the
test skips when the artifact is absent. Note also that the runner is a **standalone developer script**
(`python research/_test_cumulative_usage.py`) — its underscore-prefixed filename is not collected by
pytest, so none of these checks run in CI; enforcement is developer-run.

## Related

- The **codex prefix-cache probe** (separate finding): cold `codex exec` gets **zero** cross-invocation
  cache on user-prompt content, and a unique per-invocation `turn_id` UUID is what breaks prefix reuse —
  relevant when reasoning about *why* uncached never favored the bounded arm.

## Still owed

- **Public numbers-correction PR** (roadmap item #2): `README.md`, `docs/BENCHMARKS.md`, and the wiki
  still carry the refuted **4.19×/1.97×** and **9.69×** figures. This document explains the correction;
  the published surfaces have **not** yet been updated to match.
