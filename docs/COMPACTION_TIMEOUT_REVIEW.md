# Review — Compaction-Timeout censoring in the long-chain test

**Reviewer:** Claude (independent code review) · **Date:** 2026-06-18
**Branch:** `claude/long-chain-test-vulnerability-crcert`
**Subject:** `research/chain_test_v2.py` + `research/agentic_variety_test.py` +
`research/analyze_uncached_cost.py`
**Verdict:** **Confirmed real.** Measurement-integrity defect, not a security
CVE. Severity **High** for any cross-arm claim drawn from a long chain;
negligible for short (≤5-turn) chains.

---

## 1. What I was asked to review

A suspected vulnerability in the long-chain harness: that Claude Code
auto-compaction on long native-resume chains causes per-turn timeouts whose
usage is censored and silently dropped from aggregation, biasing the headline
comparisons. This is my own assessment of whether that holds, how bad it is, and
whether the code's existing guards are enough.

## 2. Verdict up front

The defect is **real and reproducible by code inspection**. The harness already
contains partial defenses (it *counts* and *warns about* censored turns) but
they are **insufficient**: the bias is not corrected, the asymmetry is invisible
in the primary output, and the lost data is unrecoverable on the claude path.
The existing `1800 → 2400` cap bump is a frequency band-aid, not a fix. I would
not publish a `chain5` cross-arm ratio on the current code without the
corrections in §6.

---

## 3. What I verified

| Claim | Evidence | Holds? |
|-------|----------|--------|
| Per-turn cap covers task **and** compaction time, undifferentiated | `subprocess.run(..., timeout=CLAUDE_TIMEOUT_SEC)` — single cap, no split (`agentic_variety_test.py:234-239`) | ✅ |
| Timed-out turn's usage is destroyed | `TimeoutExpired` → `out_json.write_text("{}")` (`agentic_variety_test.py:243-250`); claude only emits its result object at end-of-turn, so the partial is unrecoverable | ✅ |
| Censored turns are excluded from every mean | `aggregate_runs` filters via `turn_timed_out(...)`; `n_runs` = non-censored count (`chain_test_v2.py:1094-1120`); mirrored in `analyze_uncached_cost.py:74-100` | ✅ |
| Exposure is **asymmetric** across arms | Native arms resume the full transcript turn-over-turn (`builtin_session_id` threaded, `chain_test_v2.py:883-970`); bounded arms send a fresh capped prompt and never resume | ✅ |
| Bias points in PromptPilot's favour | The censored (compaction-heavy, expensive) turns belong to the *native* arms PromptPilot is measured against; dropping them deflates those arms' uncached/`$` | ✅ |
| Only warned, never corrected | `analyze_chain` prints `!! uncached ratio UNRELIABLE … lower bound` but still prints the ratio (`analyze_uncached_cost.py:213-223`); `print_chain_summary` shows no censored column at all (`chain_test_v2.py:1147-1254`) | ✅ |

### Documented live occurrence
`chain1` T1 pegged the cap on **5/5** `slm_native` runs at the old 1800s cap,
zeroing tokens while still earning file-hash success — the reason the cap was
raised (`agentic_variety_test.py:98-104`). That is the same mechanism, already
observed in the wild, just on a short chain's cold-start turn rather than a long
chain's compaction turns.

---

## 4. Why short chains hid it

`CLAUDE_TIMEOUT_SEC` was "originally calibrated to chain4 5-turn cases"
(`agentic_variety_test.py:103`). On ≤5 turns the resumed transcript rarely fills
the window, so auto-compaction seldom fires and the censor is rare and roughly
symmetric. `chain5` is 15 turns by design (long-task referential decay,
`chain_test_v2.py:320-334`); its later turns sit past where compaction begins,
so the censor concentrates on the native arms' tail turns. The bug is therefore
**structurally invisible to the test suite the defaults were tuned on** — which
is exactly why it warrants a standalone review rather than a one-line cap bump.

---

## 5. Findings (severity-ranked)

**F1 — Asymmetric exclusion biases cross-arm ratios. [High]**
Excluding censored turns is a non-random, arm-correlated deletion. Per-turn
denominators (`n_runs`) differ per arm, so totals are not over the same work,
and the native arms' uncached/`$` are understated. Any `WITH vs slm_native` /
`WITH vs builtin` headline from a long chain is biased.

**F2 — Lost data is unrecoverable on the claude path. [High]**
The `{}` overwrite (`agentic_variety_test.py:249`) discards even a partial
result. Codex timeouts are recoverable from incremental JSONL
(`analyze_uncached_cost.py:57-63`); claude's are not. So the correction the
analyzer asks for ("run recover_uncached.py for the recovered/imputed figure")
has nothing to recover *from* on claude.

**F3 — Asymmetry is invisible in the primary summary. [Medium]**
`print_chain_summary` surfaces no `timed_out_count`. A reader of the headline
table cannot see that one arm lost turns; the only warning lives in a separate
analysis pass that still prints the suspect ratio anyway.

**F4 — All-censored turn reads as a clean zero. [Medium]**
When every run of a turn times out, `aggregate_runs` emits `n_runs=0`,
`all_timed_out=True`, zeroed means (`chain_test_v2.py:1118-1121`) — rendered as
`0.00`, indistinguishable from a real failure, with no flag.

**F5 — Cap-raising is treated as the remedy. [Low]**
The `1800 → 2400` bump reduces *frequency* on one known turn but does not
eliminate the asymmetry, recover lost tokens, or protect `chain5`'s tail
(`agentic_variety_test.py:98-104`). It is a stopgap that currently stands in for
a fix.

---

## 6. Recommendation

Order of value, lowest-risk first. (Concrete, but this is a review — I have not
implemented these.)

1. **Make censoring visible and the ratio honest (addresses F1, F3, F4).** Add a
   per-arm `TO` column to the primary summary; render all-censored turns as
   `n/a` not `0`; and in `analyze_chain` **suppress** (don't print) any
   cross-arm uncached/`$` ratio when the two arms' censored counts differ
   materially, rather than printing it with a caveat.
2. **Recover instead of destroy (addresses F2).** Only write `{}` when the file
   is genuinely empty; preserve any partial output and attempt a salvage parse
   so a claude timeout can land in the existing `recovered` bucket like codex.
   Where nothing is salvageable, impute from the same turn's surviving runs and
   label it — never fold imputed values into the clean mean.
3. **Attack the root cause (addresses F5).** Tag turns that compacted and
   account compaction wall-time as infrastructure, not task time, so the cap is
   judged against task work and the asymmetry disappears at source. Keep the
   2400s cap as a floor, documented as frequency-reduction only.
4. **Guard it.** Add a regression test that feeds asymmetric `timed_out` records
   across two arms and asserts the summary surfaces the counts, the ratio is
   suppressed, and all-censored turns render `n/a`.

## 7. Scope notes

- The `chain1` T3 inheritance artifact (`chain_test_v2.py:146-162`) is a
  separate, already-documented issue and out of scope here.
- Codex-side timeout handling is already recoverable and not implicated.
- No paid re-runs are needed to act on this review; re-measurement is a
  follow-up once the corrections land.
