# Compaction-Timeout Fix Plan — long-chain test (`chain_test_v2.py`)

**Status:** proposed · **Owner branch:** `claude/long-chain-test-vulnerability-crcert`
**Scope:** `research/chain_test_v2.py`, `research/agentic_variety_test.py`,
`research/analyze_uncached_cost.py`
**Class:** measurement-integrity (benchmark-honesty) bug — *not* a security CVE.

---

## 1. Summary

The long-chain harness has a silent measurement bias that appears only on
**long chains** (`chain5`, 15 turns) and only on the **native-resume arms**
(`builtin`, `slm_native`, `stacked`). When Claude Code's **auto-compaction**
fires mid-turn, the compaction summarization burns wall-clock on top of the
task work and pushes the turn over the fixed per-turn cap
(`CLAUDE_TIMEOUT_SEC`). The turn is killed (`rc=124`), its usage JSON is
overwritten to `{}` and **lost**, and downstream aggregation **excludes** the
turn from every mean while only *counting* it.

Because timeout exposure is **asymmetric across arms**, this exclusion is a
non-random, arm-correlated deletion. It systematically drops the native arms'
slowest and most expensive turns out of the denominator, deflating their
reported uncached tokens and `$`, and biasing the headline session-mechanism
ratios (`WITH vs slm_native`, `WITH vs builtin`) in PromptPilot's favour. This
is the same class of error the harness already guards against for
`DISABLE_MICROCOMPACT` and the raised `model_context_window` artifact
(`chain_test_v2.py:59-93`), but on the timeout axis it is currently only
*warned about*, never *corrected*.

---

## 2. Why it only bites long chains

| Arm | Continuity mechanism | Transcript growth | Compaction exposure |
|-----|----------------------|-------------------|---------------------|
| `no_session` / `raw` | none — fresh prompt each turn | flat | ~none |
| `with_session` / `gated_session` | PromptPilot **bounded** summary in-prompt | flat (bounded) | ~none |
| `builtin` / `slm_native` / `stacked` | tool **native** `claude --resume` | grows every turn | rises with chain length |

The bounded arms send a fresh, size-capped prompt on every turn, so they never
resume and never trigger auto-compaction. The native-resume arms replay and
extend the full transcript each turn (`run_chain_once` carries
`builtin_session_id` across turns, `chain_test_v2.py:883-970`). On a 5-turn
chain the transcript rarely fills the window; on `chain5`'s 15 turns it does,
auto-compaction fires on the later turns, and those turns are exactly the ones
that get censored. The bias is therefore **invisible on the short chains** the
defaults were calibrated against (`CLAUDE_TIMEOUT_SEC` was "originally
calibrated to chain4 5-turn cases", `agentic_variety_test.py:103`) and only
emerges on the long-chain test.

---

## 3. Root-cause walk-through (with line refs)

1. **Per-turn wall cap.** `run_claude_code` runs `claude` under
   `subprocess.run(..., timeout=CLAUDE_TIMEOUT_SEC)`
   (`agentic_variety_test.py:234-239`). `CLAUDE_TIMEOUT_SEC` defaults to `2400`
   (`agentic_variety_test.py:104`). The cap covers **task work + any
   auto-compaction the resume triggers** — they are not separated.

2. **Lossy kill.** On `TimeoutExpired` the handler overwrites the output file
   with `{}` and returns `rc=124` (`agentic_variety_test.py:243-250`). Because
   `claude --output-format json` only emits its single result object at the
   *end* of the turn, a killed turn has no usable JSON — and the `{}` overwrite
   discards even a partial one. The in-code comment calls this
   **"UNRECOVERABLE"** for claude, in contrast to codex's incremental JSONL
   which a late `turn.completed` can recover
   (`agentic_variety_test.py:98-104`, `analyze_uncached_cost.py:57-63`).

3. **Censor tag.** `run_chain_once` sets `score["censored"] = timed_out`
   (`chain_test_v2.py:976-977`). The file-hash success the turn *did* earn
   before the kill is preserved in the raw record, but its tokens/cost read ~0.

4. **Asymmetric exclusion.** `aggregate_runs` filters censored turns out of
   **every** mean via `turn_timed_out(...)` and reports them only as
   `timed_out_count`; `n_runs` becomes the **non-censored** count
   (`chain_test_v2.py:1094-1120`). `analyze_uncached_cost._arm_stats` does the
   same exclude-and-count (`analyze_uncached_cost.py:74-100`). The classifier
   `turn_timed_out` is deterministic for new files (carries `timeout_cap_sec`):
   `wall >= 0.97*cap AND uncached <= 0` (`agentic_variety_test.py:376-396`).

5. **Warned, not corrected.** `analyze_chain` prints
   `!! uncached ratio UNRELIABLE … the more-censored arm is deflated — this is a
   lower bound` (`analyze_uncached_cost.py:213-223`), but it **still prints the
   ratio**. `print_chain_summary` in the harness itself does **not surface
   `timed_out_count` at all** (`chain_test_v2.py:1147-1254`), so a reader of the
   primary table never sees the asymmetry.

6. **All-censored turn vanishes.** If every run of a turn times out,
   `aggregate_runs` emits `n_runs=0`, `all_timed_out=True`, and **zeroed means**
   (`chain_test_v2.py:1118-1121`) — the turn silently contributes nothing and is
   not flagged in the summary table.

### Failure trace (concrete)

> `chain1` T1 hit the cap on **5/5** `slm_native` runs at the old 1800s cap,
> zeroing tokens (`'{}'` overwrite, *UNRECOVERABLE*) while still earning
> file-hash success — which is why the cap was bumped `1800 → 2400`
> (`agentic_variety_test.py:98-104`).

That bump is a calibration band-aid: it lowers the *frequency* of the censor on
one known turn but does not remove the asymmetry, does not recover the lost
tokens, and does not protect the longer `chain5` whose later turns sit well past
where compaction begins.

---

## 4. Impact

- **Biased headline ratios.** `WITH vs slm_native` and `WITH vs builtin`
  uncached/`$` ratios understate the native arms because their censored
  (compaction-heavy, expensive) turns are dropped from the mean. The bias points
  in PromptPilot's favour.
- **Non-comparable denominators.** Arms are averaged over different turn counts
  (`n_runs` differs per turn per arm), so per-arm totals are not over the same
  work.
- **Invisible in the primary view.** The harness summary table shows no
  censored-turn column; only the separate `analyze_uncached_cost` pass warns,
  and even there it still prints the unreliable number.
- **Silent zero turns.** An all-censored turn reads as a clean 0 with no flag.

---

## 5. Fix plan (phased)

Design goal: **make compaction-induced timeouts visible, recoverable, and
unable to silently bias a cross-arm comparison** — never just suppress the
symptom by raising the cap.

### P1 — Stop the silent bias (correctness; do first)

- **P1.1 Surface censoring in the primary summary.** Add a `TO` (timed-out)
  column per arm to `print_chain_summary` and a per-arm censored-turn total to
  the verdict block (`chain_test_v2.py:1147-1254`). A reader of the headline
  table must see the asymmetry without running the separate analyzer.
- **P1.2 Promote the "UNRELIABLE" warning to a hard guard.** In
  `analyze_chain` (`analyze_uncached_cost.py:202-223`), when the two arms'
  `timed_out` counts differ by more than a small threshold (e.g. `> 1`), **do
  not print a bare ratio** — print `RATIO SUPPRESSED (asymmetric censoring:
  N vs M)` and require the recovered/imputed figure instead. Symmetric censoring
  (equal counts) may still print with the lower-bound caveat.
- **P1.3 Flag all-censored turns.** When `all_timed_out=True`
  (`chain_test_v2.py:1118-1121`), render the turn as `n/a` (not `0.00`) in the
  table so it cannot be read as a real zero, and exclude it from the verdict's
  per-turn denominator explicitly.

### P2 — Recover the lost data (close the "UNRECOVERABLE" gap)

- **P2.1 Stop destroying partial output.** In the `TimeoutExpired` handler
  (`agentic_variety_test.py:243-250`), only write `{}` when the file is empty;
  if `claude` wrote any partial JSON/stream before the kill, **preserve it** and
  attempt a salvage parse so a timed-out turn can carry recovered usage like
  codex already does. (Pair with `--output-format stream-json` if a single
  end-of-turn JSON object cannot be salvaged.)
- **P2.2 Route recovered turns to the `recovered` bucket, not the clean mean.**
  `analyze_uncached_cost` already separates `recovered` (timeout but
  `uncached > 0`) from `censored` (`analyze_uncached_cost.py:78-85`,
  `:106-108`); P2.1 simply makes claude turns eligible for that bucket. No new
  bucket needed — wire claude's salvaged usage through `_turn_uncached`.
- **P2.3 Imputation fallback.** For turns that remain genuinely censored
  (no salvage possible), impute uncached/`$` from the same turn's non-censored
  runs (or the arm's neighbouring turns) so the cross-arm ratio uses an
  upper-bound-corrected figure rather than `0`. Keep imputed values clearly
  labelled and never fold them into the "clean" mean.

### P3 — Separate compaction cost from the cap (root cause)

- **P3.1 Tag turns that compacted.** Detect the compaction event from the
  claude transcript (compaction/summary marker) and record `compacted: true` +
  the compaction token/`$` cost on the turn. This makes "did this turn pay a
  compaction tax?" an explicit, auditable field rather than an inference from
  wall time.
- **P3.2 Compaction-aware budget.** Treat compaction wall-time as
  infrastructure, not task time: either (a) measure task-only wall separately so
  the cap is judged against task work, or (b) give native-resume arms a cap that
  scales with transcript length / expected compaction count. This removes the
  asymmetry at its source instead of compensating downstream.
- **P3.3 Calibration as stopgap only.** The `1800 → 2400` bump
  (`agentic_variety_test.py:98-104`) stays as a floor, but the plan must not
  rely on cap-raising alone — document that it reduces frequency, not bias.

### P4 — Regression guard

- **P4.1 Unit test the aggregation invariant.** Synthesize per-turn records with
  asymmetric `timed_out` across two arms and assert: (a) the summary surfaces the
  `TO` counts (P1.1), (b) the ratio is suppressed when censoring is asymmetric
  (P1.2), (c) an all-censored turn renders `n/a` not `0` (P1.3). This belongs
  next to the existing harness tests under `tests/`.
- **P4.2 Document in MEASUREMENT_METHODOLOGY.** Add a short "compaction-timeout
  censoring" subsection cross-linking this plan, mirroring the existing
  timeout-aware-measurement notes.

---

## 6. Acceptance criteria

1. Running `chain5` on `claude-code` with the native arms surfaces per-arm
   censored-turn counts in the **primary** summary table.
2. When two compared arms have materially different censored-turn counts, the
   analyzer **suppresses** (does not print) a bare uncached/`$` ratio and points
   at the recovered/imputed path.
3. A claude turn killed mid-compaction no longer loses *all* usage where a
   partial result existed — it lands in the `recovered` bucket.
4. An all-censored turn never reads as a clean `0.00` in any table.
5. New regression test covers the three rendering/guard invariants and passes.

---

## 7. Out of scope

- Changing chain definitions or `expected_files` (the `chain1` T3 inheritance
  artifact at `chain_test_v2.py:146-162` is a separate, documented issue).
- Re-running paid baselines — this plan changes harness/analysis code and adds
  guards; re-measurement is a follow-up once the fixes land.
- Codex-side timeout handling, which is already recoverable via incremental
  JSONL (`analyze_uncached_cost.py:57-63`).
